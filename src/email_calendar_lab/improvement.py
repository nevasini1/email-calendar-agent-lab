from __future__ import annotations

from .agent import AgentConfig
from .models import AgentRun
from .subagents import ImprovementProposer


def propose_candidate(current: AgentConfig, failures: list[AgentRun]) -> AgentConfig:
    return ImprovementProposer().propose(current, failures)


def propose_rejected_candidate(current: AgentConfig) -> AgentConfig:
    rules = tuple((*current.prompt_rules, "answer_fast_without_new_evidence"))
    return AgentConfig(name=f"{current.name}+bad-candidate", prompt_rules=rules, model=current.model)


def acceptance_decision(
    current_score: dict,
    candidate_score: dict,
    current_heldout: dict,
    candidate_heldout: dict,
) -> tuple[bool, str]:
    improves = candidate_score["score"] > current_score["score"]
    no_regression = candidate_heldout["score"] >= current_heldout["score"] and _no_category_regression(
        current_heldout, candidate_heldout
    )
    if improves and no_regression:
        return True, "accepted: improved generated+stable eval score without heldout regression"
    if not improves:
        return False, "rejected: did not improve generated+stable eval score"
    return False, "rejected: heldout regression"


def _no_category_regression(current: dict, candidate: dict) -> bool:
    for category, current_bucket in current.get("by_category", {}).items():
        candidate_bucket = candidate.get("by_category", {}).get(category, {"passed": 0, "total": current_bucket["total"]})
        current_rate = current_bucket["passed"] / current_bucket["total"] if current_bucket["total"] else 0
        candidate_rate = candidate_bucket["passed"] / candidate_bucket["total"] if candidate_bucket["total"] else 0
        if candidate_rate < current_rate:
            return False
    return True


def prompt_text(config: AgentConfig) -> str:
    rules = "\n".join(f"- {rule}" for rule in config.prompt_rules)
    return f"""Model: {config.model}
Agent: {config.name}

Rules:
{rules}
"""

