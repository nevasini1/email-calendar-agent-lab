from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .agent import AgentConfig
from .fixtures import HELDOUT_EVALS, PRODUCTION_SCENARIOS, STABLE_EVALS
from .harness import HarnessCore, HarnessResult
from .models import AgentRun, EvalCase, Scenario
from .subagents import EvalFactory


def run_suite(config: AgentConfig, scenarios: tuple[Scenario, ...]) -> list[AgentRun]:
    return [result.run for result in run_suite_results(config, scenarios)]


def run_suite_results(config: AgentConfig, scenarios: tuple[Scenario, ...]) -> list[HarnessResult]:
    harness = HarnessCore(config)
    return [harness.execute(scenario, mode="build") for scenario in scenarios]


def score_runs(runs: list[AgentRun], *, scenario_catalog: tuple[Scenario, ...] | None = None) -> dict:
    passed = sum(run.passed for run in runs)
    total = len(runs)
    by_category: dict[str, dict[str, int]] = {}
    if scenario_catalog is None:
        catalog: tuple[Scenario, ...] = (*PRODUCTION_SCENARIOS, *STABLE_EVALS, *HELDOUT_EVALS)
    else:
        catalog = scenario_catalog
    scenario_lookup = {scenario.id: scenario for scenario in catalog}
    for run in runs:
        category = scenario_lookup[run.scenario_id].category if run.scenario_id in scenario_lookup else infer_category(run.scenario_id)
        bucket = by_category.setdefault(category, {"passed": 0, "total": 0})
        bucket["passed"] += int(run.passed)
        bucket["total"] += 1
    return {"passed": passed, "total": total, "score": round(passed / total, 3) if total else 0.0, "by_category": by_category}


def infer_category(scenario_id: str) -> str:
    if scenario_id.startswith("prod_model_") or scenario_id.startswith("stable_model_") or scenario_id.startswith(
        "heldout_model_"
    ):
        return "general"
    if "next_meeting" in scenario_id or "cancelled" in scenario_id:
        return "cancelled_events"
    if "last_sync" in scenario_id:
        return "attendees_vs_senders"
    if "flight" in scenario_id:
        return "flight_emails"
    if "free_time_alex" in scenario_id:
        return "ambiguous_contacts"
    if "sarah_before_offsite" in scenario_id:
        return "last_before_anchor"
    if "recurring" in scenario_id:
        return "recurring_meetings"
    if "timezone" in scenario_id:
        return "time_zones"
    return "unknown"


def failures_to_evals(runs: list[AgentRun], scenarios: tuple[Scenario, ...]) -> list[EvalCase]:
    return EvalFactory().from_failures(runs, scenarios)


def load_eval_cases(path: Path) -> list[EvalCase]:
    if not path.is_file():
        return []
    return [eval_case_from_row(row) for row in load_jsonl(path)]


def eval_case_from_row(row: dict[str, Any]) -> EvalCase:
    return EvalCase(
        id=str(row["id"]),
        query=str(row["query"]),
        expected_contains=_tuple_of_str(row.get("expected_contains")),
        category=str(row["category"]),
        source_failure=str(row.get("source_failure") or "unknown"),
        expected_tools=_tuple_of_str(row.get("expected_tools")),
        expected_evidence_ids=_tuple_of_str(row.get("expected_evidence_ids")),
        forbidden_contains=_tuple_of_str(row.get("forbidden_contains")),
        required_tool_args=_tool_args(row.get("required_tool_args")),
        lifecycle=row.get("lifecycle", "candidate"),
        origin_run_id=row.get("origin_run_id"),
        root_cause=row.get("root_cause"),
        reflection_id=row.get("reflection_id"),
        lesson_type=row.get("lesson_type"),
        promotion_status=row.get("promotion_status", "quarantined"),
        first_seen_at=row.get("first_seen_at"),
        seen_count=int(row.get("seen_count") or 1),
    )


def merge_generated_eval_cases(existing: list[EvalCase], fresh: list[EvalCase]) -> list[EvalCase]:
    merged: dict[tuple[str, str, tuple[str, ...]], EvalCase] = {}
    order: list[tuple[str, str, tuple[str, ...]]] = []
    for case in existing:
        signature = eval_case_signature(case)
        if signature not in merged:
            order.append(signature)
        merged[signature] = case
    for case in fresh:
        signature = eval_case_signature(case)
        previous = merged.get(signature)
        if previous is None:
            merged[signature] = case
            order.append(signature)
            continue
        merged[signature] = replace(
            previous,
            source_failure=case.source_failure or previous.source_failure,
            origin_run_id=case.origin_run_id or previous.origin_run_id,
            root_cause=case.root_cause or previous.root_cause,
            seen_count=max(previous.seen_count + 1, case.seen_count),
        )
    return [merged[signature] for signature in order]


def eval_case_signature(case: EvalCase) -> tuple[str, str, tuple[str, ...]]:
    return (case.query.strip().lower(), case.category, case.expected_evidence_ids)


def eval_cases_to_scenarios(evals: list[EvalCase]) -> tuple[Scenario, ...]:
    return tuple(
        Scenario(
            id=case.id,
            query=case.query,
            expected_contains=case.expected_contains,
            category=case.category,
            expected_tools=case.expected_tools,
            split="stable",
            expected_evidence_ids=case.expected_evidence_ids,
            forbidden_contains=case.forbidden_contains,
            required_tool_args=case.required_tool_args,
        )
        for case in evals
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, default=str) + "\n" for row in rows))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSONL: {exc}") from exc
    return rows


def validate_eval_files(paths: tuple[Path, ...]) -> dict[str, int]:
    required = {"id", "query", "expected_contains", "category", "expected_tools"}
    generated_required = required | {"lifecycle", "expected_evidence_ids", "origin_run_id", "root_cause"}
    workflow_required = {
        "id",
        "query",
        "expected_workflow_type",
        "expected_evidence_ids",
        "expected_action_types",
        "forbidden_side_effects",
    }
    counts = {}
    for path in paths:
        rows = load_jsonl(path)
        for index, row in enumerate(rows, start=1):
            if path.name == "generated.jsonl":
                required_keys = generated_required
            elif path.name == "workflow.jsonl":
                required_keys = workflow_required
            else:
                required_keys = required
            missing = required_keys - set(row)
            if missing:
                raise ValueError(f"{path}:{index} missing required keys: {sorted(missing)}")
            if path.name == "workflow.jsonl":
                if not isinstance(row["expected_evidence_ids"], list):
                    raise ValueError(f"{path}:{index} expected_evidence_ids must be a list")
                if not isinstance(row["expected_action_types"], list):
                    raise ValueError(f"{path}:{index} expected_action_types must be a list")
                continue
            if not isinstance(row["expected_contains"], list):
                raise ValueError(f"{path}:{index} expected_contains must be a list")
            if path.name == "generated.jsonl" and row.get("lifecycle") != "candidate":
                raise ValueError(f"{path}:{index} generated eval lifecycle must be candidate")
        counts[path.name] = len(rows)
    return counts


def run_to_dict(run: AgentRun) -> dict:
    data = asdict(run)
    data["tool_calls"] = [asdict(call) for call in run.tool_calls]
    return data


def _tuple_of_str(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str))


def _tool_args(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    args: dict[str, dict[str, Any]] = {}
    for tool, spec in raw.items():
        if isinstance(tool, str) and isinstance(spec, dict):
            args[tool] = dict(spec)
    return args
