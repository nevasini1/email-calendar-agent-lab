from __future__ import annotations

from dataclasses import dataclass

from .agent import AgentConfig
from .models import AgentRun, EvalCase, Scenario

CATEGORY_RULES = {
    "cancelled_events": "exclude_cancelled_events",
    "attendees_vs_senders": "prefer_human_participants",
    "flight_emails": "parse_flight_destination",
    "ambiguous_contacts": "clarify_ambiguous_contacts",
    "last_before_anchor": "respect_temporal_anchors",
    "time_zones": "preserve_source_timezones",
}


@dataclass(frozen=True)
class TraceDecision:
    scenario_id: str
    passed: bool
    root_cause: str | None
    failure_reason: str | None
    evidence_ids: tuple[str, ...]


class TraceEvaluator:
    """Local subagent that summarizes a run trace into a root-cause decision."""

    def evaluate(self, run: AgentRun, scenario: Scenario) -> TraceDecision:
        evidence_ids = tuple(dict.fromkeys(evidence_id for call in run.tool_calls for evidence_id in call.evidence_ids))
        return TraceDecision(
            scenario_id=run.scenario_id,
            passed=run.passed,
            root_cause=run.root_cause or (None if run.passed else scenario.category),
            failure_reason=run.failure_reason,
            evidence_ids=evidence_ids,
        )


class EvalFactory:
    """Turns observed failures into deduped candidate evals."""

    def __init__(self, trace_evaluator: TraceEvaluator | None = None) -> None:
        self.trace_evaluator = trace_evaluator or TraceEvaluator()

    def from_failures(self, runs: list[AgentRun], scenarios: tuple[Scenario, ...]) -> list[EvalCase]:
        scenario_by_id = {scenario.id: scenario for scenario in scenarios}
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        evals = []
        for run in runs:
            if run.passed:
                continue
            scenario = scenario_by_id[run.scenario_id]
            decision = self.trace_evaluator.evaluate(run, scenario)
            signature = (scenario.query.lower(), scenario.category, scenario.expected_evidence_ids)
            if signature in seen:
                continue
            seen.add(signature)
            evals.append(
                EvalCase(
                    id=f"generated_{scenario.id}",
                    query=scenario.query,
                    expected_contains=scenario.expected_contains,
                    category=scenario.category,
                    source_failure=decision.failure_reason or "unknown",
                    expected_tools=scenario.expected_tools,
                    expected_evidence_ids=scenario.expected_evidence_ids,
                    forbidden_contains=scenario.forbidden_contains,
                    required_tool_args=scenario.required_tool_args,
                    lifecycle="candidate",
                    origin_run_id=run.scenario_id,
                    root_cause=decision.root_cause or scenario.category,
                )
            )
        return evals


class ImprovementProposer:
    """Subagent-style prompt rule proposer driven by root causes."""

    def propose(self, current: AgentConfig, failures: list[AgentRun]) -> AgentConfig:
        rules = list(current.prompt_rules)
        for failure in failures:
            rule = CATEGORY_RULES.get(failure.root_cause or "")
            if rule and rule not in rules:
                rules.append(rule)
        return AgentConfig(name=f"{current.name}+candidate", prompt_rules=tuple(rules), model=current.model)

