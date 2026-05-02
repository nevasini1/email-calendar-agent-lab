"""Compact per-run summaries appended to logs/run_history.jsonl for dashboard charts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _score_snap(score: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": score.get("passed"),
        "total": score.get("total"),
        "score": score.get("score"),
        "by_category": score.get("by_category") or {},
    }


def build_run_history_entry(log: dict[str, Any]) -> dict[str, Any]:
    wf = log["workflow_reliability"]["score"]
    prod = log["production_failure_discovery"]["score"]
    discovery = log["production_failure_discovery"]
    imp = log["self_improvement"]
    rej = log["rejected_candidate"]
    lf = log["langfuse_export"]
    rfl = log["reflective_phase"]["langfuse_export"]
    current_config = log.get("current_config") or {}
    reflective = log.get("reflective_phase") or {}
    sessions = log.get("session_logs") or {}
    runtime = log.get("runtime") or {}
    return {
        "run_at": log["run_at"],
        "runtime": {
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "backend": runtime.get("backend"),
            "sample_type": "live_openai" if runtime.get("provider") == "openai-live" else "deterministic",
        },
        "eval_suite_sources": log.get("eval_suite_sources") or {},
        "current_config_source": current_config.get("source"),
        "current_config_loaded": current_config.get("loaded") or {},
        "current_config_final": current_config.get("final") or {},
        "workflow": _score_snap(wf),
        "production_baseline": _score_snap(prod),
        "fresh_generated_eval_count": discovery.get("fresh_generated_eval_count", discovery.get("generated_eval_count")),
        "carried_generated_eval_count": discovery.get("carried_generated_eval_count", 0),
        "active_generated_eval_count": discovery.get("active_generated_eval_count", discovery.get("generated_eval_count")),
        "generated_eval_count": discovery["generated_eval_count"],
        "suite_baseline": _score_snap(imp["current_eval_score"]),
        "suite_candidate": _score_snap(imp["candidate_eval_score"]),
        "heldout_baseline": _score_snap(imp["current_heldout_score"]),
        "heldout_candidate": _score_snap(imp["candidate_heldout_score"]),
        "suite_category_delta": imp.get("category_delta") or {},
        "heldout_category_delta": imp.get("heldout_category_delta") or {},
        "sanity_bad_suite": _score_snap(rej["eval_score"]),
        "sanity_bad_heldout": _score_snap(rej["heldout_score"]),
        "sanity_gate_bad_accepted": rej["accepted"],
        "sanity_gate_decision": rej["decision"],
        "promotion_accepted": imp["accepted"],
        "promotion_decision": imp["decision"],
        "session_log_count": sessions.get("count"),
        "reflection_count": reflective.get("count"),
        "reflection_generalizable_count": reflective.get("generalizable_count"),
        "candidate_skill_count": len(log.get("candidate_skills") or []),
        "prompt_rules_baseline_n": len(imp["current_prompt_rules"]),
        "prompt_rules_candidate_n": len(imp["candidate_prompt_rules"]),
        "baseline_rules": list(imp["current_prompt_rules"]),
        "candidate_rules": list(imp["candidate_prompt_rules"]),
        "langfuse_sessions": {
            "enabled": lf.get("enabled"),
            "exported": lf.get("exported"),
            "errors_count": len(lf.get("errors") or []),
        },
        "langfuse_reflective": {
            "enabled": rfl.get("enabled"),
            "exported": rfl.get("exported"),
            "errors_count": len(rfl.get("errors") or []),
        },
    }


def append_run_history_line(log_dir: Path, entry: dict[str, Any]) -> None:
    path = log_dir / "run_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
