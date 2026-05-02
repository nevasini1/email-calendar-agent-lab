from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .agent import BASELINE_CONFIG
from .pipeline_progress import emit
from .dspy_gepa import DspyGepaBridge
from .evals import (
    eval_case_signature,
    eval_cases_to_scenarios,
    failures_to_evals,
    load_eval_cases,
    merge_generated_eval_cases,
    run_suite_results,
    run_to_dict,
    score_runs,
    validate_eval_files,
    write_jsonl,
)
from .evolution import EvolutionRunner
from .production_model_scenarios import resolve_heldout_scenarios, resolve_production_scenarios, resolve_stable_scenarios
from .improvement import (
    acceptance_decision,
    config_to_dict,
    load_current_config,
    prompt_text,
    propose_candidate,
    propose_rejected_candidate,
    write_current_config,
)
from .langfuse_exporter import LangfuseSessionExporter
from .memory import MemoryStore
from .reflection import ReflectivePhase
from .run_history import append_run_history_line, build_run_history_entry
from .session_store import SessionStore
from .skills import SkillMiner
from .subagents import TraceEvaluator
from .workflow_evals import WORKFLOW_EVALS, run_workflow_evals

ROOT = Path(__file__).resolve().parents[2]
# Eval/trace backend label for run logs; actual export is Langfuse when keys are present.
DEFAULT_EVAL_BACKEND = "langfuse"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / ".env.langfuse.local")
    eval_dir = ROOT / "evals"
    log_dir = ROOT / "logs"
    prompt_dir = ROOT / "prompts"
    session_store = SessionStore(log_dir / "sessions")
    memory_store = MemoryStore(ROOT / "memory" / "email_calendar_lab.sqlite")
    for directory in (eval_dir, log_dir, prompt_dir):
        directory.mkdir(parents=True, exist_ok=True)

    current, current_config_source = load_current_config(prompt_dir, BASELINE_CONFIG)
    emit("starting", step=1, message="Starting cycle — workflow slice …")
    emit(
        "config_load",
        step=1,
        message="Loaded current prompt config for this iteration",
        detail={"source": current_config_source, "prompt_rules": list(current.prompt_rules)},
    )
    emit("workflow_run", step=1, message="Running workflow JSONL evals …")
    workflow_reliability = run_workflow_evals()
    emit(
        "workflow",
        step=1,
        message="Workflow evals finished",
        detail=dict(workflow_reliability.get("score", {})),
    )
    production_scenarios, production_scenario_source = resolve_production_scenarios()
    stable_scenarios, stable_scenario_source = resolve_stable_scenarios()
    heldout_scenarios, heldout_scenario_source = resolve_heldout_scenarios()

    emit(
        "production_run",
        step=2,
        message=f"Running {len(production_scenarios)} production-like scenarios ({production_scenario_source}) …",
    )
    production_results = run_suite_results(current, production_scenarios)
    production_runs = [result.run for result in production_results]
    trace_evaluator = TraceEvaluator()
    production_scenario_by_id = {scenario.id: scenario for scenario in production_scenarios}
    for run in production_runs:
        scenario = production_scenario_by_id.get(run.scenario_id)
        if scenario is None:
            continue
        run.root_cause = trace_evaluator.evaluate(run, scenario).root_cause
    emit(
        "production",
        step=2,
        message="Production scenarios evaluated",
        detail=dict(score_runs(production_runs, scenario_catalog=production_scenarios)),
    )
    fresh_generated = failures_to_evals(production_runs, production_scenarios)
    fresh_generated_signatures = {eval_case_signature(case) for case in fresh_generated}
    carried_generated = load_eval_cases(eval_dir / "generated.jsonl")
    generated = merge_generated_eval_cases(carried_generated, fresh_generated)
    active_generated = [case for case in generated if case.promotion_status != "rejected"]
    generated_scenarios = eval_cases_to_scenarios(active_generated)
    emit(
        "generated_evals",
        step=2,
        message="Failures converted and merged into generated.jsonl rows",
        detail={
            "fresh_generated_eval_count": len(fresh_generated),
            "carried_generated_eval_count": len(carried_generated),
            "generated_eval_count": len(generated),
            "active_generated_eval_count": len(active_generated),
        },
    )

    eval_suite = (*stable_scenarios, *generated_scenarios)
    emit(
        "baseline_suite",
        step=3,
        message=(
            f"Scoring baseline on stable ∪ generated (stable={stable_scenario_source}) "
            f"and held-out ({heldout_scenario_source}) …"
        ),
    )
    current_eval_results = run_suite_results(current, eval_suite)
    current_eval_runs = [result.run for result in current_eval_results]
    current_heldout_results = run_suite_results(current, heldout_scenarios)
    current_heldout_runs = [result.run for result in current_heldout_results]

    current_score = score_runs(current_eval_runs, scenario_catalog=eval_suite)
    emit(
        "suite_baseline",
        step=3,
        message="Baseline scored on stable ∪ generated",
        detail=dict(current_score),
    )

    rejected_candidate = propose_rejected_candidate(current)
    emit("rejected_suite", step=4, message="Scoring weak variant on suite + held-out …")
    rejected_eval_results = run_suite_results(rejected_candidate, eval_suite)
    rejected_eval_runs = [result.run for result in rejected_eval_results]
    rejected_heldout_results = run_suite_results(rejected_candidate, heldout_scenarios)
    rejected_heldout_runs = [result.run for result in rejected_heldout_results]
    rejected_score = score_runs(rejected_eval_runs, scenario_catalog=eval_suite)
    rejected_heldout = score_runs(rejected_heldout_runs, scenario_catalog=heldout_scenarios)
    rejected_accepted, rejected_decision = acceptance_decision(
        score_runs(current_eval_runs, scenario_catalog=eval_suite),
        rejected_score,
        score_runs(current_heldout_runs, scenario_catalog=heldout_scenarios),
        rejected_heldout,
    )
    emit(
        "sanity_gate",
        step=4,
        message="Sanity gate (intentionally worse candidate)",
        detail={"accepted": rejected_accepted, "decision": rejected_decision},
    )

    emit("candidate_propose", step=5, message="Proposing candidate from production failures …")
    candidate = propose_candidate(current, [run for run in production_runs if not run.passed])
    emit("candidate_suite", step=5, message="Scoring candidate on suite + held-out …")
    candidate_eval_results = run_suite_results(candidate, eval_suite)
    candidate_eval_runs = [result.run for result in candidate_eval_results]
    candidate_heldout_results = run_suite_results(candidate, heldout_scenarios)
    candidate_heldout_runs = [result.run for result in candidate_heldout_results]

    candidate_score = score_runs(candidate_eval_runs, scenario_catalog=eval_suite)
    current_heldout = score_runs(current_heldout_runs, scenario_catalog=heldout_scenarios)
    candidate_heldout = score_runs(candidate_heldout_runs, scenario_catalog=heldout_scenarios)
    accepted, decision = acceptance_decision(current_score, candidate_score, current_heldout, candidate_heldout)
    emit(
        "candidate_scored",
        step=5,
        message="Candidate vs baseline — scoring complete",
        detail={
            "baseline_eval": dict(current_score),
            "candidate_eval": dict(candidate_score),
            "accepted": accepted,
            "decision": decision,
        },
    )
    final_config = candidate if accepted else current
    emit(
        "promotion",
        step=6,
        message="Promotion decision applied",
        detail={"accepted": accepted, "final_is_candidate": accepted},
    )

    emit("artifacts", step=7, message="Writing evals, prompts, sessions, Langfuse, reflection …")
    write_jsonl(eval_dir / "stable.jsonl", [scenario_to_eval_row(scenario) for scenario in stable_scenarios])
    write_jsonl(eval_dir / "heldout.jsonl", [scenario_to_eval_row(scenario) for scenario in heldout_scenarios])
    write_jsonl(eval_dir / "generated.jsonl", [asdict(case) for case in generated])
    write_jsonl(eval_dir / "workflow.jsonl", [asdict(case) for case in WORKFLOW_EVALS])
    validation = validate_eval_files(
        (eval_dir / "stable.jsonl", eval_dir / "generated.jsonl", eval_dir / "heldout.jsonl", eval_dir / "workflow.jsonl")
    )

    (prompt_dir / "baseline.md").write_text(prompt_text(BASELINE_CONFIG))
    (prompt_dir / "rejected_candidate.md").write_text(prompt_text(rejected_candidate))
    (prompt_dir / "candidate.md").write_text(prompt_text(candidate))
    (prompt_dir / "current.md").write_text(prompt_text(final_config))
    write_current_config(prompt_dir / "current.json", final_config)
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
        if eval_case_signature(eval_case) not in fresh_generated_signatures:
            continue
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
        "runtime": {
            "provider": all_results[0].session.provider if all_results else None,
            "model": all_results[0].session.model if all_results else final_config.model,
            "backend": os.getenv("EMAIL_CALENDAR_AGENT_BACKEND", "deterministic").lower().strip(),
        },
        "eval_suite_sources": {
            "production": production_scenario_source,
            "stable": stable_scenario_source,
            "heldout": heldout_scenario_source,
        },
        "default_eval": {
            "backend": DEFAULT_EVAL_BACKEND,
            "json_mirror_enabled": True,
            "langfuse_export": langfuse_export,
        },
        "current_config": {
            "source": current_config_source,
            "loaded": config_to_dict(current),
            "final": config_to_dict(final_config),
        },
        "production_failure_discovery": {
            "score": score_runs(production_runs, scenario_catalog=production_scenarios),
            "scenario_source": production_scenario_source,
            "scenarios": [scenario_to_eval_row(s) for s in production_scenarios],
            "runs": [run_to_dict(run) for run in production_runs],
            "fresh_generated_eval_count": len(fresh_generated),
            "carried_generated_eval_count": len(carried_generated),
            "active_generated_eval_count": len(active_generated),
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
    append_run_history_line(log_dir, build_run_history_entry(log))
    emit(
        "completed",
        step=7,
        message="Cycle complete — run_latest.json written",
        detail={"accepted": accepted, "decision": decision},
    )
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
