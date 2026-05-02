from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .adaptive_reasoner import judge_acceptance
from .agent import AgentConfig
from .models import AgentRun
from .subagents import ImprovementProposer

_RULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def propose_candidate(current: AgentConfig, failures: list[AgentRun]) -> AgentConfig:
    return ImprovementProposer().propose(current, failures)


def propose_rejected_candidate(current: AgentConfig) -> AgentConfig:
    rules = tuple((*current.prompt_rules, "answer_fast_without_new_evidence"))
    return AgentConfig(name=f"{current.name}+bad-candidate", prompt_rules=rules, model=current.model)


def load_current_config(prompt_dir: Path, fallback: AgentConfig) -> tuple[AgentConfig, str]:
    """Load the last promoted config so the next cycle evaluates it as current."""
    json_path = prompt_dir / "current.json"
    md_path = prompt_dir / "current.md"
    if json_path.is_file():
        try:
            config = _config_from_mapping(json.loads(json_path.read_text()), fallback)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            config = None
        if config is not None:
            return config, str(json_path)

    if md_path.is_file():
        try:
            config = _config_from_markdown(md_path.read_text(), fallback)
        except OSError:
            config = None
        if config is not None:
            return config, str(md_path)

    return fallback, "baseline_config"


def write_current_config(path: Path, config: AgentConfig) -> None:
    path.write_text(json.dumps(config_to_dict(config), indent=2) + "\n")


def config_to_dict(config: AgentConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "model": config.model,
        "prompt_rules": list(config.prompt_rules),
    }


def acceptance_decision(
    current_score: dict,
    candidate_score: dict,
    current_heldout: dict,
    candidate_heldout: dict,
) -> tuple[bool, str]:
    min_gain = float(os.getenv("ACCEPTANCE_MIN_GAIN", "0.02"))

    # Hard guardrails: never accept held-out regressions.
    if candidate_heldout["score"] < current_heldout["score"] or not _no_category_regression(current_heldout, candidate_heldout):
        return False, "rejected by guardrail: heldout regression"

    # Main-suite guardrail: no per-category regressions either.
    if not _no_category_regression(current_score, candidate_score):
        return False, "rejected by guardrail: suite category regression"

    # Require meaningful aggregate gain before model can accept.
    if candidate_score["score"] < current_score["score"] + min_gain - 1e-9:
        return False, f"rejected by guardrail: suite gain < {min_gain:.2f}"

    model_accept, model_reason = judge_acceptance(
        current_score=current_score,
        candidate_score=candidate_score,
        current_heldout=current_heldout,
        candidate_heldout=candidate_heldout,
    )
    if model_accept is not None:
        verdict = "accepted by model" if model_accept else "rejected by model"
        suffix = f": {model_reason}" if model_reason else ""
        return model_accept, f"{verdict}{suffix}"

    # Deterministic fallback if model judgment is unavailable.
    improves = candidate_score["score"] > current_score["score"]
    if improves:
        return True, "accepted: improved generated+stable eval score without heldout regression"
    return False, "rejected: did not improve generated+stable eval score"


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


def _config_from_mapping(payload: dict[str, Any], fallback: AgentConfig) -> AgentConfig | None:
    raw_rules = payload.get("prompt_rules") or payload.get("rules")
    if not isinstance(raw_rules, list):
        return None
    rules = _clean_rules(raw_rules)
    if not rules:
        return None
    name = payload.get("name") or payload.get("agent") or fallback.name
    model = payload.get("model") or fallback.model
    if not isinstance(name, str) or not isinstance(model, str):
        return None
    return AgentConfig(name=name.strip() or fallback.name, prompt_rules=rules, model=model.strip() or fallback.model)


def _config_from_markdown(text: str, fallback: AgentConfig) -> AgentConfig | None:
    name = fallback.name
    model = fallback.model
    raw_rules: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Model:"):
            model = stripped.partition(":")[2].strip() or model
        elif stripped.startswith("Agent:"):
            name = stripped.partition(":")[2].strip() or name
        elif stripped.startswith("- "):
            raw_rules.append(stripped[2:].strip())
    rules = _clean_rules(raw_rules)
    if not rules:
        return None
    return AgentConfig(name=name, prompt_rules=rules, model=model)


def _clean_rules(raw_rules: list[Any]) -> tuple[str, ...]:
    rules: list[str] = []
    for item in raw_rules:
        if not isinstance(item, str):
            continue
        rule = item.strip().lower().replace(" ", "_").replace("-", "_")
        if _RULE_NAME_RE.match(rule) and rule not in rules:
            rules.append(rule)
    return tuple(rules)
