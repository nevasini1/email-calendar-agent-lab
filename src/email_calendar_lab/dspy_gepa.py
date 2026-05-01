from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import EvalCase
from .reflection import ReflectionRecord
from .skills import CandidateSkill


@dataclass(frozen=True)
class GepaArtifact:
    name: str
    seed_text: str
    objective: str
    actionable_side_information: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DspyGepaBridge:
    """DSPy/GEPA backend bridge for reflective prompt evolution.

    The lab always prepares GEPA-ready artifacts and actionable side information.
    The backend is enabled by default; deterministic GEPA-lite remains the local
    fallback when dependencies or reflection LM configuration are unavailable.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.enabled = os.getenv("DSPY_GEPA_ENABLED", "true").lower() not in {"0", "false", "no"}
        self.reflection_lm = os.getenv("DSPY_GEPA_REFLECTION_LM")

    def prepare_artifacts(
        self,
        reflections: list[ReflectionRecord],
        candidate_evals: list[EvalCase],
        candidate_skills: list[CandidateSkill],
    ) -> list[GepaArtifact]:
        failure_feedback = tuple(
            self._reflection_feedback(reflection)
            for reflection in reflections
            if reflection.generalizes and not reflection.passed
        )
        skill_feedback = tuple(
            f"Candidate skill {skill.id} is quarantined; validate on {', '.join(skill.validation_evals)}."
            for skill in candidate_skills
        )
        eval_feedback = tuple(
            f"Candidate eval {eval_case.id} targets {eval_case.root_cause}; expected evidence {eval_case.expected_evidence_ids}."
            for eval_case in candidate_evals
        )
        return [
            GepaArtifact(
                name="prompt_rules",
                seed_text="Improve prompt rules for email/calendar reasoning while preserving tool evidence constraints.",
                objective="Maximize generated+stable eval score without heldout regression.",
                actionable_side_information=(*failure_feedback, *eval_feedback),
            ),
            GepaArtifact(
                name="skill_text",
                seed_text="Improve skill documents for recurring failure categories without overfitting to proper nouns.",
                objective="Create reusable skill text that passes category evals and heldout checks.",
                actionable_side_information=(*failure_feedback, *skill_feedback),
            ),
            GepaArtifact(
                name="tool_descriptions",
                seed_text="Clarify Gmail and Calendar tool descriptions so the agent uses correct filters and evidence.",
                objective="Reduce tool misuse such as cancelled event inclusion and missing time bounds.",
                actionable_side_information=failure_feedback,
            ),
        ]

    def maybe_run(
        self,
        reflections: list[ReflectionRecord],
        candidate_evals: list[EvalCase],
        candidate_skills: list[CandidateSkill],
    ) -> dict[str, Any]:
        artifacts = self.prepare_artifacts(reflections, candidate_evals, candidate_skills)
        status: dict[str, Any] = {
            "backend": "dspy_gepa",
            "enabled": self.enabled,
            "active": False,
            "artifacts": [artifact.to_dict() for artifact in artifacts],
            "log_dir": str(self.log_dir),
        }
        if not self.enabled:
            status["reason"] = "DSPY_GEPA_ENABLED explicitly disabled; using deterministic reflective GEPA-lite fallback"
            return status
        try:
            import dspy  # type: ignore
        except ImportError:
            status.update({"reason": "dspy is not installed; run python3 -m pip install -e ."})
            return status
        try:
            import gepa  # type: ignore  # noqa: F401
        except ImportError:
            status.update({"reason": "gepa is not installed; run python3 -m pip install -e ."})
            return status
        if not self.reflection_lm:
            status.update({"reason": "DSPY_GEPA_REFLECTION_LM is required to run dspy.GEPA"})
            return status
        status["api"] = "dspy.GEPA"
        status["reflection_lm"] = self.reflection_lm
        status["active"] = True
        status["skipped_compile"] = True
        status["reason"] = (
            "DSPy/GEPA dependencies are available, but this lab keeps compile disabled until a DSPy program "
            "wrapper is supplied; artifacts and ASI are ready for compile()."
        )
        status["example_compile_shape"] = {
            "optimizer": "dspy.GEPA(metric=metric_with_feedback, max_metric_calls=20, reflection_lm=...)",
            "metric_signature": "metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> score|feedback",
        }
        return status

    @staticmethod
    def _reflection_feedback(reflection: ReflectionRecord) -> str:
        return (
            f"{reflection.scenario_id}: lesson={reflection.lesson_type}; root_cause={reflection.root_cause}; "
            f"summary={reflection.summary}; evidence={reflection.evidence_ids}; "
            "Do not overfit to names, dates, or airport codes; preserve task schema and heldout behavior."
        )

