from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .agent import BASELINE_CONFIG
from .dspy_gepa import DspyGepaBridge
from .evals import (
    eval_cases_to_scenarios,
    failures_to_evals,
    run_suite_results,
    run_to_dict,
    score_runs,
    validate_eval_files,
    write_jsonl,
)
from .evolution import EvolutionRunner
from .fixtures import HELDOUT_EVALS, PRODUCTION_SCENARIOS, STABLE_EVALS
from .improvement import acceptance_decision, prompt_text, propose_candidate, propose_rejected_candidate
from .langfuse_exporter import LangfuseSessionExporter
from .memory import MemoryStore
from .reflection import ReflectivePhase
from .session_store import SessionStore
from .skills import SkillMiner
from .workflow_evals import WORKFLOW_EVALS, run_workflow_evals

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVAL_BACKEND = "langfuse"


def main() -> None:
    eval_dir = ROOT / "evals"
    log_dir = ROOT / "logs"
    prompt_dir = ROOT / "prompts"
    session_store = SessionStore(log_dir / "sessions")
    memory_store = MemoryStore(ROOT / "memory" / "email_calendar_lab.sqlite")
    for directory in (eval_dir, log_dir, prompt_dir):
        directory.mkdir(parents=True, exist_ok=True)

    current = BASELINE_CONFIG
    workflow_reliability = run_workflow_evals()
    production_results = run_suite_results(current, PRODUCTION_SCENARIOS)
    production_runs = [result.run for result in production_results]
    generated = failures_to_evals(production_runs, PRODUCTION_SCENARIOS)
    generated_scenarios = eval_cases_to_scenarios(generated)

    eval_suite = (*STABLE_EVALS, *generated_scenarios)
    current_eval_results = run_suite_results(current, eval_suite)
    current_eval_runs = [result.run for result in current_eval_results]
    current_heldout_results = run_suite_results(current, HELDOUT_EVALS)
    current_heldout_runs = [result.run for result in current_heldout_results]

    rejected_candidate = propose_rejected_candidate(current)
    rejected_eval_results = run_suite_results(rejected_candidate, eval_suite)
    rejected_eval_runs = [result.run for result in rejected_eval_results]
    rejected_heldout_results = run_suite_results(rejected_candidate, HELDOUT_EVALS)
    rejected_heldout_runs = [result.run for result in rejected_heldout_results]
    rejected_score = score_runs(rejected_eval_runs)
    rejected_heldout = score_runs(rejected_heldout_runs)
    rejected_accepted, rejected_decision = acceptance_decision(
        score_runs(current_eval_runs),
        rejected_score,
        score_runs(current_heldout_runs),
        rejected_heldout,
    )

    candidate = propose_candidate(current, [run for run in production_runs if not run.passed])
    candidate_eval_results = run_suite_results(candidate, eval_suite)
    candidate_eval_runs = [result.run for result in candidate_eval_results]
    candidate_heldout_results = run_suite_results(candidate, HELDOUT_EVALS)
    candidate_heldout_runs = [result.run for result in candidate_heldout_results]

    current_score = score_runs(current_eval_runs)
    candidate_score = score_runs(candidate_eval_runs)
    current_heldout = score_runs(current_heldout_runs)
    candidate_heldout = score_runs(candidate_heldout_runs)
    accepted, decision = acceptance_decision(current_score, candidate_score, current_heldout, candidate_heldout)
    final_config = candidate if accepted else current

    write_jsonl(eval_dir / "stable.jsonl", [scenario_to_eval_row(scenario) for scenario in STABLE_EVALS])
    write_jsonl(eval_dir / "heldout.jsonl", [scenario_to_eval_row(scenario) for scenario in HELDOUT_EVALS])
    write_jsonl(eval_dir / "generated.jsonl", [asdict(case) for case in generated])
    write_jsonl(eval_dir / "workflow.jsonl", [asdict(case) for case in WORKFLOW_EVALS])
    validation = validate_eval_files(
        (eval_dir / "stable.jsonl", eval_dir / "generated.jsonl", eval_dir / "heldout.jsonl", eval_dir / "workflow.jsonl")
    )

    (prompt_dir / "baseline.md").write_text(prompt_text(BASELINE_CONFIG))
    (prompt_dir / "rejected_candidate.md").write_text(prompt_text(rejected_candidate))
    (prompt_dir / "candidate.md").write_text(prompt_text(candidate))
    (prompt_dir / "current.md").write_text(prompt_text(final_config))
    session_paths = session_store.save_many(
        [
            *production_results,
            *current_eval_results,
            *current_heldout_results,
            *rejected_eval_results,
            *rejected_heldout_results,
            *candidate_eval_results,
            *candidate_heldout_results,
        ]
    )
    all_results = [
        *production_results,
        *current_eval_results,
        *current_heldout_results,
        *rejected_eval_results,
        *rejected_heldout_results,
        *candidate_eval_results,
        *candidate_heldout_results,
    ]
    langfuse = LangfuseSessionExporter()
    langfuse_export = langfuse.export_many(all_results)
    reflections = ReflectivePhase().reflect_many(all_results, langfuse_export)
    reflection_by_scenario = {reflection.scenario_id: reflection for reflection in reflections}
    for eval_case in generated:
        reflection = reflection_by_scenario.get(eval_case.origin_run_id or "")
        if reflection:
            eval_case.reflection_id = reflection.id
            eval_case.lesson_type = reflection.lesson_type
            eval_case.first_seen_at = reflection.created_at
            eval_case.promotion_status = "quarantined"
    write_jsonl(eval_dir / "generated.jsonl", [asdict(case) for case in generated])
    validation = validate_eval_files(
        (eval_dir / "stable.jsonl", eval_dir / "generated.jsonl", eval_dir / "heldout.jsonl", eval_dir / "workflow.jsonl")
    )
    candidate_skills = SkillMiner().mine(reflections)
    evolution_summary = EvolutionRunner().run(
        reflections,
        generated,
        candidate_skills,
        current_score,
        candidate_score,
        current_heldout,
        candidate_heldout,
        DspyGepaBridge(log_dir=log_dir / "gepa"),
    )
    reflective_langfuse_export = langfuse.export_reflective_phase(reflections, evolution_summary)
    for result in all_results:
        memory_store.remember_session(result)
    for reflection in reflections:
        memory_store.remember_reflection(reflection)
        if reflection.generalizes:
            memory_store.remember_lesson(reflection, reflection.recommended_artifact, "quarantined")
    for evolution_decision in evolution_summary["decisions"]:
        memory_store.remember_promotion(
            evolution_decision["artifact_id"],
            evolution_decision["artifact_type"],
            evolution_decision["status"],
            evolution_decision["reason"],
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
    memory_store.commit()
    memory_summary = memory_store.summary()
    memory_store.close()

    log = {
        "run_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "default_eval": {
            "backend": DEFAULT_EVAL_BACKEND,
            "json_mirror_enabled": True,
            "langfuse_export": langfuse_export,
        },
        "production_failure_discovery": {
            "score": score_runs(production_runs),
            "runs": [run_to_dict(run) for run in production_runs],
            "generated_eval_count": len(generated),
            "generated_evals": [asdict(case) for case in generated],
        },
        "workflow_reliability": workflow_reliability,
        "eval_validation": validation,
        "session_logs": {
            "count": len(session_paths),
            "paths": session_paths,
        },
        "langfuse_export": langfuse_export,
        "reflective_phase": {
            "count": len(reflections),
            "generalizable_count": sum(1 for reflection in reflections if reflection.generalizes),
            "records": [reflection.to_dict() for reflection in reflections],
            "langfuse_export": reflective_langfuse_export,
        },
        "memory": memory_summary,
        "candidate_skills": [skill.to_dict() for skill in candidate_skills],
        "candidate_eval_promotions": [
            decision
            for decision in evolution_summary["decisions"]
            if decision["artifact_type"] == "eval"
        ],
        "evolution_decisions": evolution_summary,
        "rejected_candidate": {
            "prompt_rules": rejected_candidate.prompt_rules,
            "eval_score": rejected_score,
            "heldout_score": rejected_heldout,
            "decision": rejected_decision,
            "accepted": rejected_accepted,
        },
        "self_improvement": {
            "current_prompt_rules": current.prompt_rules,
            "candidate_prompt_rules": candidate.prompt_rules,
            "current_eval_score": current_score,
            "candidate_eval_score": candidate_score,
            "category_delta": category_delta(current_score, candidate_score),
            "current_heldout_score": current_heldout,
            "candidate_heldout_score": candidate_heldout,
            "heldout_category_delta": category_delta(current_heldout, candidate_heldout),
            "decision": decision,
            "accepted": accepted,
        },
        "before_after_eval_runs": {
            "before": [run_to_dict(run) for run in current_eval_runs],
            "after": [run_to_dict(run) for run in candidate_eval_runs],
        },
        "heldout_runs": {
            "before": [run_to_dict(run) for run in current_heldout_runs],
            "after": [run_to_dict(run) for run in candidate_heldout_runs],
        },
    }
    (log_dir / "run_latest.json").write_text(json.dumps(log, indent=2, default=str))
    print_summary(log)


def scenario_to_eval_row(scenario) -> dict:
    return {
        "id": scenario.id,
        "query": scenario.query,
        "expected_contains": scenario.expected_contains,
        "category": scenario.category,
        "expected_tools": scenario.expected_tools,
        "split": scenario.split,
        "expected_evidence_ids": scenario.expected_evidence_ids,
        "forbidden_contains": scenario.forbidden_contains,
        "required_tool_args": scenario.required_tool_args,
        "lifecycle": scenario.split,
    }


def category_delta(before: dict, after: dict) -> dict:
    categories = set(before.get("by_category", {})) | set(after.get("by_category", {}))
    delta = {}
    for category in sorted(categories):
        before_bucket = before.get("by_category", {}).get(category, {"passed": 0, "total": 0})
        after_bucket = after.get("by_category", {}).get(category, {"passed": 0, "total": 0})
        delta[category] = {
            "before": before_bucket,
            "after": after_bucket,
            "passed_delta": after_bucket["passed"] - before_bucket["passed"],
        }
    return delta


def print_summary(log: dict) -> None:
    discovery = log["production_failure_discovery"]["score"]
    improvement = log["self_improvement"]
    print("Self-improving email/calendar lab")
    print(f"default eval backend: {log['default_eval']['backend']}")
    print(f"production discovery: {discovery['passed']}/{discovery['total']} passed")
    print(f"generated evals: {log['production_failure_discovery']['generated_eval_count']}")
    print(
        "workflow evals: "
        f"{log['workflow_reliability']['score']['passed']}/{log['workflow_reliability']['score']['total']} passed"
    )
    print(f"eval validation: {log['eval_validation']}")
    print(f"session logs: {log['session_logs']['count']}")
    print(f"langfuse export: {log['langfuse_export']}")
    print(f"reflections: {log['reflective_phase']['count']}")
    print(f"memory: {log['memory']['path']}")
    print(f"candidate skills: {len(log['candidate_skills'])}")
    print(f"rejected candidate: {log['rejected_candidate']['decision']}")
    print(f"before eval score: {improvement['current_eval_score']['score']}")
    print(f"after eval score: {improvement['candidate_eval_score']['score']}")
    print(f"heldout before/after: {improvement['current_heldout_score']['score']} -> {improvement['candidate_heldout_score']['score']}")
    print(improvement["decision"])


if __name__ == "__main__":
    main()

