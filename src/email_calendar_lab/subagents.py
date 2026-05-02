from __future__ import annotations

from dataclasses import dataclass

from .adaptive_reasoner import infer_root_cause, propose_prompt_rules
from .agent import AgentConfig
from .models import AgentRun, EvalCase, Scenario

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
        root_cause = infer_root_cause(run, scenario.category)
        return TraceDecision(
            scenario_id=run.scenario_id,
            passed=run.passed,
            root_cause=root_cause,
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
        rules = propose_prompt_rules(current.prompt_rules, failures)
        return AgentConfig(name=f"{current.name}+candidate", prompt_rules=rules, model=current.model)

