from __future__ import annotations

import json
from dataclasses import asdict
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


def score_runs(runs: list[AgentRun]) -> dict:
    passed = sum(run.passed for run in runs)
    total = len(runs)
    by_category: dict[str, dict[str, int]] = {}
    scenario_lookup = {scenario.id: scenario for scenario in (*PRODUCTION_SCENARIOS, *STABLE_EVALS, *HELDOUT_EVALS)}
    for run in runs:
        category = scenario_lookup[run.scenario_id].category if run.scenario_id in scenario_lookup else infer_category(run.scenario_id)
        bucket = by_category.setdefault(category, {"passed": 0, "total": 0})
        bucket["passed"] += int(run.passed)
        bucket["total"] += 1
    return {"passed": passed, "total": total, "score": round(passed / total, 3) if total else 0.0, "by_category": by_category}


def infer_category(scenario_id: str) -> str:
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
    counts = {}
    for path in paths:
        rows = load_jsonl(path)
        for index, row in enumerate(rows, start=1):
            required_keys = generated_required if path.name == "generated.jsonl" else required
            missing = required_keys - set(row)
            if missing:
                raise ValueError(f"{path}:{index} missing required keys: {sorted(missing)}")
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

