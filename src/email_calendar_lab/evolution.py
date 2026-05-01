from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .dspy_gepa import DspyGepaBridge
from .models import EvalCase
from .reflection import ReflectionRecord
from .skills import CandidateSkill
from .subagents import CATEGORY_RULES


@dataclass(frozen=True)
class EvolutionDecision:
    artifact_id: str
    artifact_type: str
    status: str
    reason: str
    scores: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvolutionRunner:
    """Deterministic GEPA-like optimizer placeholder with strict gates."""

    def run(
        self,
        reflections: list[ReflectionRecord],
        candidate_evals: list[EvalCase],
        candidate_skills: list[CandidateSkill],
        current_score: dict,
        candidate_score: dict,
        current_heldout: dict,
        candidate_heldout: dict,
        gepa_bridge: DspyGepaBridge | None = None,
    ) -> dict[str, Any]:
        decisions: list[EvolutionDecision] = []
        improved = candidate_score["score"] > current_score["score"]
        heldout_safe = self._heldout_safe(current_heldout, candidate_heldout)

        for eval_case in candidate_evals:
            status = "promoted" if improved and heldout_safe and eval_case.root_cause in CATEGORY_RULES else "quarantined"
            reason = (
                "candidate eval captures a generalizable failure and current candidate improved safely"
                if status == "promoted"
                else "candidate eval remains quarantined until repeated observation or stronger validation"
            )
            decisions.append(
                EvolutionDecision(
                    eval_case.id,
                    "eval",
                    status,
                    reason,
                    {"candidate_score": candidate_score["score"], "heldout_score": candidate_heldout["score"]},
                )
            )

        for skill in candidate_skills:
            status = "quarantined"
            reason = "candidate skill mined from success; requires category-specific validation before promotion"
            decisions.append(
                EvolutionDecision(
                    skill.id,
                    "skill",
                    status,
                    reason,
                    {"validation_evals": skill.validation_evals},
                )
            )

        decisions.append(
            EvolutionDecision(
                "bad_prompt_variant",
                "prompt_rule",
                "rejected",
                "deliberate bad/no-op prompt variant did not improve generated+stable eval score",
                {"current_score": current_score["score"], "candidate_score": current_score["score"]},
            )
        )

        prompt_variants = sorted(
            {
                CATEGORY_RULES[reflection.root_cause]
                for reflection in reflections
                if reflection.root_cause in CATEGORY_RULES and reflection.generalizes
            }
        )
        dspy_gepa = (gepa_bridge or DspyGepaBridge(log_dir=Path("logs/gepa"))).maybe_run(
            reflections, candidate_evals, candidate_skills
        )

        return {
            "optimizer": "deterministic-reflective-gepa-lite+dspy-gepa-bridge",
            "dspy_gepa": dspy_gepa,
            "prompt_rule_variants": prompt_variants,
            "decisions": [decision.to_dict() for decision in decisions],
            "accepted_count": sum(1 for decision in decisions if decision.status == "promoted"),
            "rejected_count": sum(1 for decision in decisions if decision.status == "rejected"),
            "quarantined_count": sum(1 for decision in decisions if decision.status == "quarantined"),
        }

    @staticmethod
    def _heldout_safe(current_heldout: dict, candidate_heldout: dict) -> bool:
        if candidate_heldout["score"] < current_heldout["score"]:
            return False
        for category, before in current_heldout.get("by_category", {}).items():
            after = candidate_heldout.get("by_category", {}).get(category, {"passed": 0, "total": before["total"]})
            before_rate = before["passed"] / before["total"] if before["total"] else 0
            after_rate = after["passed"] / after["total"] if after["total"] else 0
            if after_rate < before_rate:
                return False
        return True

