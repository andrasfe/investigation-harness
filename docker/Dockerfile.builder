# scout-builder: JDK + Maven + Gradle image used by scout to run
# real mvn/gradle/jacoco commands against target repos.
#
# Scout stays on the host; only the Java toolchain lives in here. The
# host-side python driver shells out to `docker run` with the target
# repo bind-mounted at /workspace. See scout/docker_runner.py.
#
# Caches: .m2 and .gradle are bind-mounted from the host at runtime so
# dependency downloads persist across runs.

FROM eclipse-temurin:17-jdk-jammy

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    MAVEN_VERSION=3.9.9 \
    GRADLE_VERSION=8.10.2 \
    MAVEN_HOME=/opt/maven \
    GRADLE_HOME=/opt/gradle \
    PATH=/opt/maven/bin:/opt/gradle/bin:$PATH

# Base tooling: git (for wrappers that fetch deps), curl, unzip, ca-certs.
# Keep the layer lean — no man pages, no recommends.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      unzip \
      procps \
    && rm -rf /var/lib/apt/lists/*

# --- Maven ---------------------------------------------------------------
RUN set -eux; \
    curl -fsSL -o /tmp/maven.tgz \
      "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz"; \
    mkdir -p /opt/maven; \
    tar -xzf /tmp/maven.tgz -C /opt/maven --strip-components=1; \
    rm /tmp/maven.tgz; \
    mvn --version

# --- Gradle --------------------------------------------------------------
RUN set -eux; \
    curl -fsSL -o /tmp/gradle.zip \
      "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip"; \
    mkdir -p /opt/gradle; \
    unzip -q /tmp/gradle.zip -d /tmp/gradle-unpack; \
    mv /tmp/gradle-unpack/gradle-${GRADLE_VERSION}/* /opt/gradle/; \
    rm -rf /tmp/gradle.zip /tmp/gradle-unpack; \
    gradle --version

# Work around git "dubious ownership" on bind-mounted checkouts — the
# workspace volume comes from the host, which has a different owner.
RUN git config --system --add safe.directory '*'

# Invocations come in as `docker run --user <uid>:<gid>` from the host so
# output files are owned by the invoking user, not root. HOME is set to
# /scout-home by the runner; cache bind mounts land at /scout-home/.m2
# and /scout-home/.gradle so Maven + Gradle defaults pick them up.
ENV GRADLE_USER_HOME=/scout-home/.gradle

# Seed writable dirs (cache roots + workspace) so a first run with
# --user 501:20 doesn't trip on `mkdir: permission denied`.
RUN mkdir -p /scout-home/.m2 /scout-home/.gradle /workspace \
    && chmod -R 0777 /scout-home /workspace

WORKDIR /workspace

# No ENTRYPOINT: callers supply the full argv via `docker run ... mvn ...`.
CMD ["bash", "-lc", "echo 'scout-builder: supply a command (mvn/gradle) to run'"]
