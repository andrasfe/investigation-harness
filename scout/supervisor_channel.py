"""Generic teacher/student IPC channel for long-running jobs.

Drop-in reference implementation. Copy into your student package; rename
module / env vars if you like. Zero dependencies beyond stdlib.

Usage (student side)::

    from your_pkg.supervisor_channel import SupervisorChannel, StudentAbort

    channel = SupervisorChannel.from_env()   # disabled unless env vars set

    # Heartbeat at natural boundaries
    channel.heartbeat({"phase": "round_12", "progress": ...})

    # Check teacher-taught rules before your own retry logic
    if channel.rules_store is not None:
        rule = channel.rules_store.match(phase=phase, msg=err, source_window=window)
        if rule:
            continue   # skip; teacher already taught this

    # Escalate on impasse
    reso = channel.escalate(
        kind="my_deadlock",
        summary="short one-line reason",
        context={...},
        artifacts=[path1, path2],
        student_hints=["look at X near line Y"],
    )

    if reso is None:
        # timeout or disabled — fall back to default abandonment
        move_on()
    elif reso.verdict == "abort":
        raise StudentAbort(reso.notes)
    elif reso.verdict == "patch":
        apply(reso.fix)


Teacher side: the channel library does not impose a specific teacher UI.
Use `teach.sh <run_dir>` (shipped alongside this file) to tail escalations,
and append one JSON line per reply to `<run_dir>/resolutions.jsonl`.

File layout::

    <run_dir>/
      escalations.jsonl     # student -> teacher, append-only
      resolutions.jsonl     # teacher -> student, append-only
      status.jsonl          # heartbeats
      teacher_rules.jsonl   # durable skip-rules
      teacher_findings.jsonl# structural observations (lint inbox)
      teacher_facts.jsonl   # program-specific domain facts

Activation (TWO gates, both required)::

    SUPERVISOR_DIR=<path>    # where the channel files live
    ESCALATE=1               # opt-in to blocking teacher round-trips

When either is missing, every method is a no-op with zero overhead.

Rename these env vars for your project if `SUPERVISOR_DIR` / `ESCALATE`
conflict with something you already use.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

SUPERVISOR_ENV = "SUPERVISOR_DIR"
ESCALATE_ENV = "ESCALATE"

ESCALATIONS_FILE = "escalations.jsonl"
RESOLUTIONS_FILE = "resolutions.jsonl"
STATUS_FILE = "status.jsonl"
RULES_FILE = "teacher_rules.jsonl"
FINDINGS_FILE = "teacher_findings.jsonl"
FACTS_FILE = "teacher_facts.jsonl"

_VALID_VERDICTS = {"patch", "skip", "abort", "restart", "retry_with"}


def _opt_in() -> bool:
    """True iff the escalate-opt-in env var is truthy."""
    return os.environ.get(ESCALATE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _append_line_with_fsync(path: Path, line: str) -> None:
    """Atomic single-line append with fsync for crash-safety."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------- exceptions
#
# These inherit from BaseException rather than Exception so that defensive
# `except Exception:` blocks elsewhere in a student pipeline do NOT
# swallow a teacher-issued abort or restart. Only explicit handlers for
# StudentAbort / StudentRestart / BaseException / bare `except:` will
# catch them — matching the convention used by KeyboardInterrupt and
# SystemExit for control-flow signals an application is not allowed to
# quietly recover from.


class StudentAbort(BaseException):
    """Teacher instructed the student to stop the run immediately.

    Callers must let this propagate out of the pipeline. If a layer
    genuinely needs to clean up before exit, it may catch this exception,
    perform cleanup, and then re-raise. Do NOT convert it to a regular
    Exception subclass or swallow it silently.
    """


class StudentRestart(BaseException):
    """Teacher requested a restart — intends to edit student source then rerun.

    Same propagation contract as StudentAbort: let it out of the
    pipeline. A wrapper script or outer driver can catch it to trigger
    the relaunch; the pipeline itself must not retry on its own.
    """


# ----------------------------------------------------------------- Resolution


@dataclass
class Resolution:
    """Reply from the teacher for a single escalation.

    Optional fields let the teacher record durable knowledge alongside
    the immediate answer:

    - ``save_rule``: pattern the student should apply autonomously next
      time instead of escalating. Appended to ``teacher_rules.jsonl``.
    - ``finding``: structural observation about the student itself (not
      a runtime fix). Appended to ``teacher_findings.jsonl`` for later
      human review.
    - ``save_fact``: program-specific domain knowledge threaded into
      the student's LLM prompts on future rounds. Appended to
      ``teacher_facts.jsonl``. Accepts a single dict or a list.
    - ``test_cases``: concrete validated input the teacher worked out
      off-line. The student runs each through its normal execution path.
      Data only, no code.
    """

    id: str
    verdict: str
    fix: dict[int, str] = field(default_factory=dict)
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    save_rule: dict[str, Any] | None = None
    finding: dict[str, Any] | None = None
    save_fact: list[dict[str, Any]] = field(default_factory=list)
    test_cases: list[dict[str, Any]] = field(default_factory=list)


# ----------------------------------------------------------------- TeacherRule


@dataclass
class TeacherRule:
    """Durable pattern the student applies to skip escalation.

    A rule matches a problem when ALL hold:
    - ``phase`` matches (exact, after student-side normalization), OR
      the rule's phase is empty/"*".
    - Every token in ``msg_contains`` is a substring of the error message.
    - If ``source_context_contains`` is set, at least one line in the
      provided source_window contains that token (case-insensitive).
    """

    kind: str  # e.g. "skip_error_class"
    phase: str
    msg_contains: list[str] = field(default_factory=list)
    source_context_contains: str = ""
    reason: str = ""
    issued_by: str = "teacher"
    ts: float = 0.0

    def matches(
        self,
        *,
        phase: str,
        msg: str,
        source_window: list[str] | None = None,
    ) -> bool:
        if self.phase and self.phase != "*" and self.phase != phase:
            return False
        for token in self.msg_contains:
            if token and token not in msg:
                return False
        if self.source_context_contains:
            needle = self.source_context_contains.upper()
            if not any(needle in line.upper() for line in source_window or []):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "phase": self.phase,
            "msg_contains": list(self.msg_contains),
            "source_context_contains": self.source_context_contains,
            "reason": self.reason,
            "issued_by": self.issued_by,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeacherRule | None":
        try:
            return cls(
                kind=str(data.get("kind", "skip_error_class")),
                phase=str(data.get("phase", "")),
                msg_contains=[str(x) for x in data.get("msg_contains", []) or []],
                source_context_contains=str(data.get("source_context_contains", "")),
                reason=str(data.get("reason", "")),
                issued_by=str(data.get("issued_by", "teacher")),
                ts=float(data.get("ts", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None


class TeacherRulesStore:
    """Append-only JSONL store of durable skip rules.

    Safe to instantiate with a missing file — reads yield an empty list,
    and ``append`` creates the file on demand.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._rules: list[TeacherRule] = []
        self.reload()

    @property
    def rules(self) -> list[TeacherRule]:
        return list(self._rules)

    def reload(self) -> None:
        self._rules = []
        if not self.path.exists():
            return
        try:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                rule = TeacherRule.from_dict(data)
                if rule is not None:
                    self._rules.append(rule)
        except OSError as exc:
            log.warning("supervisor: failed to read rules file: %s", exc)

    def append(self, rule: TeacherRule) -> None:
        try:
            _append_line_with_fsync(
                self.path, json.dumps(rule.to_dict(), default=str) + "\n"
            )
            self._rules.append(rule)
        except Exception as exc:  # noqa: BLE001
            log.warning("supervisor: failed to persist rule: %s", exc)

    def match(
        self,
        *,
        phase: str,
        msg: str,
        source_window: list[str] | None = None,
    ) -> TeacherRule | None:
        for rule in self._rules:
            if rule.matches(phase=phase, msg=msg, source_window=source_window):
                return rule
        return None


# -------------------------------------------------------------- findings store


class TeacherFindingsStore:
    """Append-only JSONL store of structural teacher observations.

    Findings are never auto-applied; they're a lint inbox for an
    operator to review between runs.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, finding: dict[str, Any]) -> None:
        payload = dict(finding)
        payload.setdefault("ts", time.time())
        try:
            _append_line_with_fsync(
                self.path, json.dumps(payload, default=str) + "\n"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("supervisor: failed to persist finding: %s", exc)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            log.warning("supervisor: failed to read findings file: %s", exc)
        return out


# ----------------------------------------------------------------- facts store


@dataclass
class TeacherFact:
    """Durable domain/system knowledge the student should apply.

    Unlike :class:`TeacherRule` (which silences known-bad signals) or
    findings (inbox items for a human), facts are *active* knowledge
    that shape value generation. The student's LLM prompt includes
    matching facts so the model produces values consistent with the
    domain without the teacher having to re-explain every round.
    """

    kind: str
    target: str
    scope: str = "variable"   # variable | paragraph | stub_op | global
    content: str = ""
    examples: list[str] = field(default_factory=list)
    reason: str = ""
    issued_by: str = "teacher"
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "scope": self.scope,
            "content": self.content,
            "examples": list(self.examples),
            "reason": self.reason,
            "issued_by": self.issued_by,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeacherFact | None":
        try:
            return cls(
                kind=str(data.get("kind", "note")),
                target=str(data.get("target", "")).strip().upper(),
                scope=str(data.get("scope", "variable")).strip().lower() or "variable",
                content=str(data.get("content", "")),
                examples=[str(x) for x in data.get("examples", []) or []],
                reason=str(data.get("reason", "")),
                issued_by=str(data.get("issued_by", "teacher")),
                ts=float(data.get("ts", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None


class TeacherFactsStore:
    """Append-only JSONL store of durable domain facts, with lookup.

    ``match()`` returns every fact whose ``scope``/``target`` matches the
    query, plus any ``global`` facts. Consumers typically pass the
    resulting list straight into an LLM prompt.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._facts: list[TeacherFact] = []
        self._last_mtime: float = 0.0
        self.reload()

    @property
    def facts(self) -> list[TeacherFact]:
        return list(self._facts)

    def reload(self) -> None:
        self._facts = []
        if not self.path.exists():
            self._last_mtime = 0.0
            return
        try:
            self._last_mtime = self.path.stat().st_mtime
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                fact = TeacherFact.from_dict(data)
                if fact is not None:
                    self._facts.append(fact)
        except OSError as exc:
            log.warning("supervisor: failed to read facts file: %s", exc)

    def reload_if_changed(self) -> bool:
        """Cheap mtime-gated reload for the hot path.

        Call this before each fact lookup so a teacher who appends a
        new fact mid-run has it applied on the next LLM call. One
        ``stat()`` per call when the store is unchanged; a full re-read
        when the mtime advances.
        """
        if not self.path.exists():
            if self._facts:
                self._facts = []
                self._last_mtime = 0.0
                return True
            return False
        try:
            current_mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if current_mtime == self._last_mtime:
            return False
        self.reload()
        return True

    def append(self, fact: TeacherFact) -> None:
        try:
            _append_line_with_fsync(
                self.path, json.dumps(fact.to_dict(), default=str) + "\n"
            )
            self._facts.append(fact)
            try:
                self._last_mtime = self.path.stat().st_mtime
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("supervisor: failed to persist fact: %s", exc)

    def match(
        self,
        *,
        scope: str,
        target: str,
    ) -> list[TeacherFact]:
        """Return facts matching this query, plus any ``global`` facts."""
        target_upper = (target or "").strip().upper()
        scope_lower = (scope or "").strip().lower()
        out: list[TeacherFact] = []
        for fact in self._facts:
            if fact.scope == "global":
                out.append(fact)
                continue
            if fact.scope == scope_lower and fact.target == target_upper:
                out.append(fact)
        return out


# --------------------------------------------------------------- channel class


class SupervisorChannel:
    """Append-only JSONL channel between a student and a teacher agent.

    Safe to instantiate even when disabled: when ``run_dir`` is None (env
    gates unsatisfied), ``escalate`` returns None immediately and
    ``heartbeat`` is a no-op. Callers construct one unconditionally and
    let the toggle live in the environment.
    """

    def __init__(
        self,
        run_dir: str | Path | None,
        *,
        poll_interval_sec: float = 1.0,
        default_timeout_sec: float = 900.0,
        pid: int | None = None,
    ):
        self.run_dir = Path(run_dir) if run_dir else None
        self.poll_interval_sec = poll_interval_sec
        self.default_timeout_sec = default_timeout_sec
        self.pid = pid if pid is not None else os.getpid()
        self._resolutions_offset = 0
        self.rules_store: TeacherRulesStore | None = None
        self.findings_store: TeacherFindingsStore | None = None
        self.facts_store: TeacherFactsStore | None = None

        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            rp = self.run_dir / RESOLUTIONS_FILE
            if rp.exists():
                self._resolutions_offset = rp.stat().st_size
            self.rules_store = TeacherRulesStore(self.run_dir / RULES_FILE)
            self.findings_store = TeacherFindingsStore(
                self.run_dir / FINDINGS_FILE
            )
            self.facts_store = TeacherFactsStore(self.run_dir / FACTS_FILE)

    @property
    def enabled(self) -> bool:
        return self.run_dir is not None

    @classmethod
    def from_env(cls, **kwargs: Any) -> "SupervisorChannel":
        """Build a channel from environment configuration.

        Two independent gates, both required for enablement:

        1. ``SUPERVISOR_DIR=<run_dir>`` — where the channel files live.
        2. ``ESCALATE=1`` — opt-in to blocking round-trips.

        If either is missing the returned channel is disabled (all
        methods are no-ops). This lets a mature student run autonomously
        even when a channel directory is available.
        """
        run_dir = os.environ.get(SUPERVISOR_ENV)
        if not run_dir or not _opt_in():
            return cls(None, **kwargs)
        return cls(run_dir, **kwargs)

    # ------------------------------------------------------------------ I/O

    def escalate(
        self,
        *,
        kind: str,
        summary: str,
        context: dict[str, Any] | None = None,
        artifacts: Iterable[str | Path] | None = None,
        student_hints: Iterable[str] | None = None,
        timeout_sec: float | None = None,
    ) -> Resolution | None:
        """Append an escalation event, block until matching resolution.

        Returns None when disabled, on timeout, or when the teacher's
        reply can't be parsed. Callers should treat None as "the teacher
        didn't answer — fall back to default behavior".
        """
        if not self.enabled:
            return None
        assert self.run_dir is not None

        event_id = str(uuid.uuid4())
        related = self._related_findings(kind, context or {})
        payload = {
            "id": event_id,
            "ts": time.time(),
            "pid": self.pid,
            "kind": kind,
            "summary": summary,
            "context": context or {},
            "artifacts": [str(p) for p in (artifacts or [])],
            "student_hints": list(student_hints or []),
            "related_findings": related,
        }
        line = json.dumps(payload, default=str) + "\n"
        escalations_path = self.run_dir / ESCALATIONS_FILE
        try:
            _append_line_with_fsync(escalations_path, line)
        except Exception as exc:  # noqa: BLE001
            log.warning("supervisor: failed to append escalation: %s", exc)
            return None

        log.warning(
            "supervisor: escalated kind=%s id=%s summary=%s",
            kind, event_id[:8], summary[:120],
        )
        deadline = time.time() + (timeout_sec or self.default_timeout_sec)
        return self._wait_for_resolution(event_id, deadline)

    def heartbeat(self, data: dict[str, Any]) -> None:
        """Append a heartbeat snapshot. Best-effort, never raises."""
        if not self.enabled:
            return
        assert self.run_dir is not None
        payload = {"ts": time.time(), "pid": self.pid, **data}
        try:
            _append_line_with_fsync(
                self.run_dir / STATUS_FILE,
                json.dumps(payload, default=str) + "\n",
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("supervisor: heartbeat failed: %s", exc)

    # ------------------------------------------------------------ internals

    def _related_findings(
        self, kind: str, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return prior findings / rules relevant to the current event.

        Keeps the list short so the escalation payload stays readable.
        The teacher sees "you already told me about this class N times"
        without having to grep through history.
        """
        if not self.findings_store and not self.rules_store:
            return []
        msg = str(context.get("error_msg", "") or context.get("msg", ""))
        phase = str(context.get("phase", ""))
        out: list[dict[str, Any]] = []

        if self.findings_store:
            for f in self.findings_store.read_all():
                blob = (str(f.get("title", "")) + " " +
                        str(f.get("notes", ""))).lower()
                if (msg and msg.lower() in blob) or (
                    phase and phase.lower() in blob
                ):
                    out.append({"kind": "finding", **f})
                if len(out) >= 8:
                    break

        if self.rules_store:
            matching = [
                r for r in self.rules_store.rules
                if (not r.phase or r.phase == "*" or r.phase == phase)
                and (not r.msg_contains or any(
                    tok and tok in msg for tok in r.msg_contains
                ))
            ]
            if matching:
                out.append({
                    "kind": "rules_summary",
                    "count": len(matching),
                    "reasons": [r.reason for r in matching[:5]],
                })
        return out

    def _persist_durable_fields(self, resolution: "Resolution") -> None:
        """Record any durable teacher knowledge carried by the resolution."""
        if resolution.save_rule and self.rules_store is not None:
            rule_data = dict(resolution.save_rule)
            rule_data.setdefault("ts", time.time())
            rule_data.setdefault("issued_by", "teacher")
            mc = rule_data.get("msg_contains")
            if isinstance(mc, str):
                rule_data["msg_contains"] = [mc]
            rule = TeacherRule.from_dict(rule_data)
            if rule is not None:
                self.rules_store.append(rule)
                log.info(
                    "supervisor: persisted teacher rule kind=%s phase=%s "
                    "msg_contains=%s",
                    rule.kind, rule.phase, rule.msg_contains,
                )
        if resolution.finding and self.findings_store is not None:
            self.findings_store.append(resolution.finding)
            log.info(
                "supervisor: persisted teacher finding: %s",
                str(resolution.finding.get("title", ""))[:120],
            )
        if resolution.save_fact and self.facts_store is not None:
            for raw in resolution.save_fact:
                fact_data = dict(raw)
                fact_data.setdefault("ts", time.time())
                fact_data.setdefault("issued_by", "teacher")
                fact = TeacherFact.from_dict(fact_data)
                if fact is None:
                    continue
                self.facts_store.append(fact)
                log.info(
                    "supervisor: persisted teacher fact scope=%s target=%s "
                    "kind=%s content=%r",
                    fact.scope, fact.target, fact.kind,
                    (fact.content[:80] + "…") if len(fact.content) > 80
                    else fact.content,
                )

    def _wait_for_resolution(
        self, event_id: str, deadline: float
    ) -> Resolution | None:
        assert self.run_dir is not None
        path = self.run_dir / RESOLUTIONS_FILE
        while time.time() < deadline:
            if path.exists():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                if size > self._resolutions_offset:
                    with open(path, "r", encoding="utf-8") as f:
                        f.seek(self._resolutions_offset)
                        # readline() (not iteration) keeps tell() usable
                        # so we can advance the cursor precisely even when
                        # a matching message is found mid-stream.
                        while True:
                            raw = f.readline()
                            if not raw:
                                break
                            pos = f.tell()
                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                self._resolutions_offset = pos
                                continue
                            if msg.get("id") == event_id:
                                self._resolutions_offset = pos
                                parsed = _parse_resolution(msg)
                                if parsed is not None:
                                    self._persist_durable_fields(parsed)
                                    return parsed
                            self._resolutions_offset = pos
            time.sleep(self.poll_interval_sec)

        log.warning(
            "supervisor: timeout waiting for resolution id=%s", event_id[:8],
        )
        return None


def _parse_resolution(msg: dict[str, Any]) -> Resolution | None:
    verdict = str(msg.get("verdict", "")).strip()
    if verdict not in _VALID_VERDICTS:
        log.warning(
            "supervisor: ignoring resolution with unknown verdict=%r", verdict,
        )
        return None
    raw_fix = msg.get("fix") or {}
    fix: dict[int, str] = {}
    if isinstance(raw_fix, dict):
        for k, v in raw_fix.items():
            try:
                fix[int(k)] = str(v)
            except (TypeError, ValueError):
                continue

    save_rule = msg.get("save_rule")
    if save_rule is not None and not isinstance(save_rule, dict):
        save_rule = None
    finding = msg.get("finding")
    if finding is not None and not isinstance(finding, dict):
        finding = None

    save_fact_raw = msg.get("save_fact")
    save_fact: list[dict[str, Any]] = []
    if isinstance(save_fact_raw, dict):
        save_fact = [save_fact_raw]
    elif isinstance(save_fact_raw, list):
        save_fact = [f for f in save_fact_raw if isinstance(f, dict)]

    tc_raw = msg.get("test_cases")
    test_cases: list[dict[str, Any]] = []
    if isinstance(tc_raw, dict):
        tc_raw = [tc_raw]
    if isinstance(tc_raw, list):
        for entry in tc_raw:
            if not isinstance(entry, dict):
                continue
            if not isinstance(entry.get("input_state"), dict):
                continue
            test_cases.append(entry)

    return Resolution(
        id=str(msg.get("id", "")),
        verdict=verdict,
        fix=fix,
        notes=str(msg.get("notes", "")),
        raw=msg,
        save_rule=save_rule,
        finding=finding,
        save_fact=save_fact,
        test_cases=test_cases,
    )
