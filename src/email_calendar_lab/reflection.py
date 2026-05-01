from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from .harness import HarnessResult

LessonType = Literal[
    "bad_temporal_reasoning",
    "bad_tool_args",
    "missing_evidence",
    "ambiguous_contact",
    "timezone_loss",
    "useful_success",
    "unknown_failure",
]


@dataclass(frozen=True)
class ReflectionRecord:
    id: str
    session_id: str
    scenario_id: str
    passed: bool
    lesson_type: LessonType
    root_cause: str | None
    generalizes: bool
    recommended_artifact: Literal["candidate_eval", "candidate_skill", "prompt_rule", "memory_only"]
    confidence: float
    summary: str
    evidence_ids: tuple[str, ...]
    langfuse_enabled: bool
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReflectivePhase:
    """Post-run analyzer that turns execution traces into reusable lessons."""

    def reflect_many(self, results: list["HarnessResult"], langfuse_export: dict[str, Any]) -> list[ReflectionRecord]:
        return [self.reflect(result, langfuse_export) for result in results]

    def reflect(self, result: "HarnessResult", langfuse_export: dict[str, Any]) -> ReflectionRecord:
        run = result.run
        lesson_type = self._lesson_type(result)
        generalizes = lesson_type != "unknown_failure"
        artifact = self._recommended_artifact(run.passed, lesson_type)
        evidence_ids = tuple(dict.fromkeys(evidence_id for call in run.tool_calls for evidence_id in call.evidence_ids))
        return ReflectionRecord(
            id=f"reflection_{uuid4().hex[:12]}",
            session_id=result.session.id,
            scenario_id=run.scenario_id,
            passed=run.passed,
            lesson_type=lesson_type,
            root_cause=run.root_cause,
            generalizes=generalizes,
            recommended_artifact=artifact,
            confidence=self._confidence(run.passed, lesson_type, evidence_ids),
            summary=self._summary(run.passed, lesson_type, run.failure_reason),
            evidence_ids=evidence_ids,
            langfuse_enabled=bool(langfuse_export.get("enabled")),
            created_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )

    @staticmethod
    def _lesson_type(result: "HarnessResult") -> LessonType:
        run = result.run
        reason = (run.failure_reason or "").lower()
        category = run.root_cause or ""
        if run.passed:
            return "useful_success"
        if category == "time_zones" or "timezone" in reason or "2:35 pm et" in reason:
            return "timezone_loss"
        if category in {"last_before_anchor", "recurring_meetings"}:
            return "bad_temporal_reasoning"
        if category == "ambiguous_contacts":
            return "ambiguous_contact"
        if "tool args" in reason or "include_cancelled" in reason:
            return "bad_tool_args"
        if "evidence" in reason or "forbidden answer" in reason:
            return "missing_evidence"
        return "unknown_failure"

    @staticmethod
    def _recommended_artifact(passed: bool, lesson_type: LessonType) -> Literal[
        "candidate_eval", "candidate_skill", "prompt_rule", "memory_only"
    ]:
        if passed:
            return "candidate_skill"
        if lesson_type in {"unknown_failure"}:
            return "memory_only"
        if lesson_type in {"bad_tool_args", "missing_evidence", "timezone_loss", "bad_temporal_reasoning", "ambiguous_contact"}:
            return "candidate_eval"
        return "prompt_rule"

    @staticmethod
    def _confidence(passed: bool, lesson_type: LessonType, evidence_ids: tuple[str, ...]) -> float:
        if passed and evidence_ids:
            return 0.72
        if lesson_type == "unknown_failure":
            return 0.35
        return 0.84 if evidence_ids else 0.68

    @staticmethod
    def _summary(passed: bool, lesson_type: LessonType, failure_reason: str | None) -> str:
        if passed:
            return f"Successful trace can be distilled as {lesson_type} pattern."
        return f"Failure classified as {lesson_type}: {failure_reason or 'unknown failure'}."

