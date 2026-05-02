from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any

from .adaptive_reasoner import judge_eval_promotion
from .dspy_gepa import DspyGepaBridge
from .models import EvalCase
from .reflection import ReflectionRecord
from .skills import CandidateSkill

_RULE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


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
            has_explained_cause = bool((eval_case.root_cause or "").strip())
            model_promote, model_reason = judge_eval_promotion(
                eval_case=eval_case,
                improved=improved,
                heldout_safe=heldout_safe,
                candidate_score=candidate_score,
                candidate_heldout=candidate_heldout,
            )
            promote = bool(model_promote) if model_promote is not None else bool(improved and heldout_safe and has_explained_cause)
            # Guardrails always apply even if model says promote.
            if not (improved and heldout_safe and has_explained_cause):
                promote = False
            status = "promoted" if promote else "quarantined"
            if status == "promoted":
                reason = model_reason or "promoted: model signaled reusable failure and guardrails passed"
            else:
                reason = model_reason or "quarantined: model/guardrails require stronger validation and measurable suite gain"
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
                f"focus_{reflection.root_cause}"
                for reflection in reflections
                if reflection.generalizes
                and reflection.root_cause
                and _RULE_RE.match(f"focus_{reflection.root_cause}")
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

