"""Dockerised execution of mvn/gradle/jacoco for scout's build tools.

Scout stays on the host; only the JDK + Maven + Gradle toolchain lives
in a container. When ``SCOUT_USE_DOCKER=1``, the ``run_build``,
``run_tests`` and ``run_coverage`` tools route their command execution
through ``run_in_container`` instead of the host ``subprocess.run``.

Design notes (why this specific shape):

1. The runner returns a ``subprocess.CompletedProcess``-shaped object so
   callers do not need a branch for "docker vs. host" — they consume
   ``returncode`` / ``stdout`` / ``stderr`` uniformly.
2. The checkout is bind-mounted at ``/workspace`` inside the container.
   The caller's argv is rewritten: any absolute-path token that lives
   under the checkout is translated to the container path; host-resolved
   binaries like ``/usr/local/bin/mvn`` are collapsed to the basename so
   the container's PATH resolves them (via /opt/maven/bin, /opt/gradle/bin).
3. Dependency caches (``~/.m2``, ``~/.gradle``) are bind-mounted from a
   host-side cache dir so deps persist across runs without baking them
   into the image.
4. The container runs as the host user (``--user UID:GID``) so files
   produced in the workspace are host-readable — prevents the classic
   root-owned-workspace footgun.
5. Network is left on (default bridge) because ``mvn test`` genuinely
   needs Maven Central on first run. If a caller wants offline mode
   they can pass ``network="none"`` explicitly.
6. A soft memory/CPU limit stops a runaway build from pinning the host.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


# ----- env knobs ---------------------------------------------------------

ENV_USE_DOCKER = "SCOUT_USE_DOCKER"
ENV_IMAGE = "SCOUT_BUILD_IMAGE"
ENV_CACHE_DIR = "SCOUT_DOCKER_CACHE_DIR"
ENV_MEM = "SCOUT_DOCKER_MEM"
ENV_CPUS = "SCOUT_DOCKER_CPUS"
ENV_NETWORK = "SCOUT_DOCKER_NETWORK"
ENV_EXTRA_ARGS = "SCOUT_DOCKER_EXTRA_ARGS"  # whitespace-split shell-quoted extras

_DEFAULT_IMAGE = "scout-builder:latest"
_DEFAULT_MEM = "4g"
_DEFAULT_CPUS = "2"
_DEFAULT_NETWORK = "bridge"
_CONTAINER_WORKDIR = "/workspace"
# HOME inside the container — we bind-mount cache dirs under it so that
# Maven (which reads $HOME/.m2) and Gradle (which honors GRADLE_USER_HOME
# but also falls back to $HOME/.gradle) both pick up the persisted cache.
_CONTAINER_HOME = "/scout-home"
_CONTAINER_M2 = "/scout-home/.m2"
_CONTAINER_GRADLE = "/scout-home/.gradle"


def is_enabled() -> bool:
    """Return True when docker-backed builds are requested via env."""
    val = os.environ.get(ENV_USE_DOCKER, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def image_name() -> str:
    return os.environ.get(ENV_IMAGE, _DEFAULT_IMAGE)


def _cache_dir() -> Path:
    raw = os.environ.get(ENV_CACHE_DIR, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".scout-docker-cache").resolve()


def ensure_caches() -> tuple[Path, Path]:
    """Create and return (m2_cache, gradle_cache) host directories."""
    root = _cache_dir()
    m2 = root / "m2"
    gradle = root / "gradle"
    m2.mkdir(parents=True, exist_ok=True)
    gradle.mkdir(parents=True, exist_ok=True)
    return m2, gradle


# ----- argv translation --------------------------------------------------

def _translate_argv(argv: Sequence[str], host_workdir: Path) -> list[str]:
    """Rewrite host-resolved argv so it works inside the container.

    Rules:
      - ``/abs/path/inside/host_workdir/x`` → ``/workspace/x``
      - ``/usr/local/bin/mvn`` (or similar absolute path outside the
        workdir, matching a known tool basename) → ``mvn``
      - relative paths and already-translated paths pass through.
    """
    host_workdir_resolved = host_workdir.resolve()
    translated: list[str] = []
    for tok in argv:
        # host checkout path → container path
        if tok.startswith("/"):
            try:
                p = Path(tok).resolve()
            except (OSError, RuntimeError):
                translated.append(tok)
                continue
            try:
                rel = p.relative_to(host_workdir_resolved)
                translated.append(f"{_CONTAINER_WORKDIR}/{rel.as_posix()}")
                continue
            except ValueError:
                pass
            # absolute tool path outside workspace — collapse to basename
            # so the container's PATH resolves it.
            base = os.path.basename(tok)
            if base in {"mvn", "mvnw", "gradle", "gradlew", "java", "javac", "git"}:
                translated.append(base)
                continue
        translated.append(tok)
    return translated


# ----- docker invocation -------------------------------------------------

@dataclass
class DockerRunResult:
    """Shaped like ``subprocess.CompletedProcess`` for drop-in compatibility."""
    returncode: int
    stdout: str
    stderr: str
    args: list[str]
    duration_sec: int
    image: str


class DockerUnavailableError(RuntimeError):
    """Raised when SCOUT_USE_DOCKER=1 but docker isn't usable."""


def _docker_bin() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise DockerUnavailableError("docker CLI not found on PATH")
    return exe


def _image_exists(image: str) -> bool:
    try:
        res = subprocess.run(
            [_docker_bin(), "image", "inspect", image],
            capture_output=True, text=True, check=False, timeout=15,
        )
        return res.returncode == 0
    except (subprocess.SubprocessError, DockerUnavailableError):
        return False


def preflight() -> None:
    """Raise ``DockerUnavailableError`` with a helpful message if not usable."""
    docker = _docker_bin()
    try:
        res = subprocess.run(
            [docker, "info"], capture_output=True, text=True, timeout=10, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise DockerUnavailableError("docker info timed out (daemon unresponsive)") from e
    if res.returncode != 0:
        raise DockerUnavailableError(
            "docker daemon not reachable — start Docker Desktop and retry.\n"
            f"docker info stderr tail: {(res.stderr or '')[-200:]}"
        )
    img = image_name()
    if not _image_exists(img):
        raise DockerUnavailableError(
            f"image '{img}' is missing — run `bash scripts/build-docker-image.sh`"
        )


def run_in_container(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    network: str | None = None,
    extra_env_passthrough: Sequence[str] = (),
) -> DockerRunResult:
    """Execute ``argv`` inside the scout-builder image.

    Parameters
    ----------
    argv
        Host-side argv. Absolute paths under ``cwd`` are rewritten to
        ``/workspace/...``; tool basenames (mvn, gradle, ...) are kept.
    cwd
        Host directory to bind-mount at ``/workspace``.
    timeout
        Hard kill timeout for the docker invocation (seconds).
    env
        Environment passed via ``-e KEY=VALUE``. Use sparingly; most
        build state is driven by args.
    network
        Override ``SCOUT_DOCKER_NETWORK`` (default: "bridge").
    extra_env_passthrough
        Names of host env vars to forward when set (e.g. ``MAVEN_OPTS``).
    """
    preflight()
    docker = _docker_bin()
    image = image_name()
    m2_cache, gradle_cache = ensure_caches()

    net = network or os.environ.get(ENV_NETWORK) or _DEFAULT_NETWORK
    mem = os.environ.get(ENV_MEM, _DEFAULT_MEM)
    cpus = os.environ.get(ENV_CPUS, _DEFAULT_CPUS)

    # Run as invoking user so files produced in the workspace are owned
    # by the caller on the host. On macOS (Docker Desktop) file perms
    # via osxfs are mapped anyway, but this is still the safer default.
    user = f"{os.getuid()}:{os.getgid()}"

    cmd = [
        docker, "run", "--rm",
        "--user", user,
        "--network", net,
        "--memory", mem,
        "--cpus", cpus,
        "--workdir", _CONTAINER_WORKDIR,
        "-v", f"{cwd.resolve()}:{_CONTAINER_WORKDIR}",
        # cache bind mounts under a writable HOME so Maven/Gradle find them
        # at their default locations ($HOME/.m2, $HOME/.gradle).
        "-v", f"{m2_cache}:{_CONTAINER_M2}",
        "-v", f"{gradle_cache}:{_CONTAINER_GRADLE}",
        "-e", f"HOME={_CONTAINER_HOME}",
        "-e", f"GRADLE_USER_HOME={_CONTAINER_GRADLE}",
        # Critical: --user <uid>:<gid> breaks Java's user.home lookup when
        # there's no matching /etc/passwd entry, so user.home resolves to
        # "?" and Maven writes deps to /workspace/?/.m2 inside the target
        # repo. Force user.home explicitly for the JVM.
        "-e", f"MAVEN_OPTS=-Duser.home={_CONTAINER_HOME}",
        "-e", f"JAVA_TOOL_OPTIONS=-Duser.home={_CONTAINER_HOME}",
    ]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    for name in extra_env_passthrough:
        val = os.environ.get(name)
        if val is not None:
            cmd += ["-e", f"{name}={val}"]

    # Optional raw extras (e.g. "-e FOO=bar --dns 1.1.1.1") advanced users
    # may slip in; parsed with shlex so quoting works.
    raw_extras = os.environ.get(ENV_EXTRA_ARGS, "").strip()
    if raw_extras:
        cmd.extend(shlex.split(raw_extras))

    cmd.append(image)
    cmd.extend(_translate_argv(argv, cwd))

    log.debug("docker_runner: invoking %s", " ".join(shlex.quote(x) for x in cmd))
    start = time.time()
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        duration = int(time.time() - start)
        # Best-effort: the docker CLI got killed; running containers may linger.
        # `docker run --rm` takes care of cleanup on container exit; after a
        # host-side kill we rely on the fact that docker daemon SIGTERMs the
        # container when the CLI dies. Emit a log so operators can sweep up.
        log.warning("docker_runner: timeout after %ds; argv=%s", duration, argv)
        return DockerRunResult(
            returncode=124,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=f"TIMEOUT after {duration}s inside docker container\n",
            args=list(cmd),
            duration_sec=duration,
            image=image,
        )
    duration = int(time.time() - start)
    return DockerRunResult(
        returncode=res.returncode,
        stdout=res.stdout or "",
        stderr=res.stderr or "",
        args=list(cmd),
        duration_sec=duration,
        image=image,
    )
