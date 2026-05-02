from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from .agent import BASELINE_CONFIG
from .harness import HarnessCore
from .memory_reflector_agent import MemoryReflectorAgent
from .models import Scenario, WorkflowPlan
from .orchestrator import Orchestrator


@contextmanager
def _temp_env(key: str, value: str):
    prior = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


@dataclass
class WorkflowEvalCase:
    """Rows written to evals/workflow.jsonl — fields must satisfy validate_eval_files workflow schema."""

    id: str
    query: str
    expected_workflow_type: str
    expected_evidence_ids: list[str]
    expected_action_types: list[str]
    forbidden_side_effects: bool = True
    expected_entities: list[str] = field(default_factory=list)


WORKFLOW_EVALS: list[WorkflowEvalCase] = [
    WorkflowEvalCase(
        id="workflow_priority_inbox_summary",
        query="Show my priority inbox",
        expected_workflow_type="priority_inbox",
        expected_evidence_ids=[
            "mail_meeting_request_maya",
            "mail_cancellation_ops",
            "mail_escalation_customer",
            "mail_attachment_briefing",
        ],
        expected_action_types=[],
        forbidden_side_effects=True,
        expected_entities=[],
    ),
    WorkflowEvalCase(
        id="workflow_meeting_request_invite",
        query="Turn Maya's launch plan meeting request into an invite",
        expected_workflow_type="meeting_request",
        expected_evidence_ids=["mail_meeting_request_maya"],
        expected_action_types=["calendar.create", "email_draft"],
        forbidden_side_effects=True,
        expected_entities=["maya", "launch"],
    ),
    WorkflowEvalCase(
        id="workflow_cancellation_update",
        query="Handle Jordan's cancellation request",
        expected_workflow_type="cancellation",
        expected_evidence_ids=["mail_cancellation_ops", "evt_ops_review"],
        expected_action_types=["calendar.cancel", "email_draft"],
        forbidden_side_effects=True,
        expected_entities=["ops", "dana"],
    ),
]


WORKFLOW_OPENAI_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="wf_openai_jordan_cancel_mail",
        query=(
            "Call gmail.search_emails once with query exactly \"jordan ops cancel\" (three keywords). "
            "From the returned messages, summarize what Jordan asks you to do about Ops review."
        ),
        category="workflow_openai",
        expected_contains=("Ops", "cancel"),
        expected_tools=("gmail.search_emails",),
        expected_evidence_ids=("mail_cancellation_ops",),
        forbidden_contains=(),
        required_tool_args={"gmail.search_emails": {"query": "jordan ops cancel"}},
    ),
    Scenario(
        id="wf_openai_ops_review_calendar",
        query=(
            "Search calendar events matching Ops review where jordan@example.com is an attendee "
            "and briefly describe what you find."
        ),
        category="workflow_openai",
        expected_contains=("Ops",),
        expected_tools=("calendar.search_events",),
        expected_evidence_ids=("evt_ops_review",),
        forbidden_contains=(),
    ),
    Scenario(
        id="wf_openai_customer_escalation",
        query="Find and summarize the customer email about the rollout being blocked.",
        category="workflow_openai",
        expected_contains=("blocked",),
        expected_tools=("gmail.search_emails",),
        expected_evidence_ids=("mail_escalation_customer",),
        forbidden_contains=(),
    ),
)


def _workflow_score_dict(*, passed_cases: int, total_cases: int) -> dict[str, Any]:
    ratio = passed_cases / total_cases if total_cases else 0.0
    return {"passed": passed_cases, "total": total_cases, "score": ratio}


def _planned_action_types(plan: WorkflowPlan) -> list[str]:
    types: list[str] = []
    for _draft in plan.drafts:
        types.append("email_draft")
    for mutation in plan.calendar_mutations:
        op = mutation.operation
        if op == "create":
            types.append("calendar.create")
        elif op == "cancel":
            types.append("calendar.cancel")
        elif op in ("update", "reschedule"):
            types.append(f"calendar.{op}")
    return types


def _entity_blob(plan: WorkflowPlan) -> str:
    parts = [plan.summary.lower()]
    for draft in plan.drafts:
        parts.append(draft.subject.lower())
        parts.append(draft.body.lower())
        parts.extend(addr.lower() for addr in draft.to)
    for mutation in plan.calendar_mutations:
        parts.append(mutation.title.lower())
        parts.extend(a.lower() for a in mutation.attendees)
    return " ".join(parts)


def score_workflow_plan(plan: WorkflowPlan, case: WorkflowEvalCase) -> tuple[bool, str | None]:
    if plan.workflow_type != case.expected_workflow_type:
        return False, f"workflow_type mismatch ({plan.workflow_type} vs {case.expected_workflow_type})"

    missing_evidence = [eid for eid in case.expected_evidence_ids if eid not in plan.evidence_ids]
    if missing_evidence:
        return False, f"missing evidence ids {missing_evidence}"

    action_types = _planned_action_types(plan)
    missing_actions = [atype for atype in case.expected_action_types if atype not in action_types]
    if missing_actions:
        return False, f"missing actions {missing_actions}"

    blob = _entity_blob(plan)
    missing_entities = [entity for entity in case.expected_entities if entity.lower() not in blob]
    if missing_entities:
        return False, f"missing entity references {missing_entities}"

    return True, None


def safety_metrics(runs: list[dict[str, Any]]) -> dict[str, int]:
    unauthorized = 0
    confirmation_required = 0
    audit_events = 0
    for run in runs:
        plan = run.get("plan")
        if not isinstance(plan, dict):
            continue
        for decision in plan.get("safety_decisions") or []:
            outcome = decision.get("outcome")
            if outcome == "BLOCK_UNAUTHORIZED_SCOPE":
                unauthorized += 1
            elif outcome == "REQUIRE_CONFIRMATION":
                confirmation_required += 1
        audit_events += len(plan.get("audit_events") or [])
    return {
        "unauthorized_blocked": unauthorized,
        "confirmation_required": confirmation_required,
        "audit_events": audit_events,
    }


def run_workflow_evals() -> dict[str, Any]:
    """
    When OPENAI_API_KEY is set, runs workflow slice via HarnessCore + OpenAI tools (real LLM).
    Otherwise runs deterministic Orchestrator plans + rule checks (offline).
    """
    if os.getenv("OPENAI_API_KEY", "").strip():
        return _run_workflow_evals_openai()
    return _run_workflow_evals_orchestrator()


def _run_workflow_evals_orchestrator() -> dict[str, Any]:
    orchestrator = Orchestrator()
    reflector = MemoryReflectorAgent()

    runs: list[dict[str, Any]] = []
    reflections: list[dict[str, Any]] = []
    passed_cases = 0

    for eval_case in WORKFLOW_EVALS:
        plan = orchestrator.route(eval_case.query)
        ok, reason = score_workflow_plan(plan, eval_case)
        if ok:
            passed_cases += 1

        reflections.append(reflector.reflect_workflow_plan(plan, ok, reason))

        runs.append(
            {
                "case": {
                    "id": eval_case.id,
                    "query": eval_case.query,
                    "expected_workflow_type": eval_case.expected_workflow_type,
                    "expected_evidence_ids": list(eval_case.expected_evidence_ids),
                    "expected_action_types": list(eval_case.expected_action_types),
                    "forbidden_side_effects": eval_case.forbidden_side_effects,
                },
                "plan": asdict(plan),
                "passed": ok,
                "failure_reason": reason,
                "eval_mode": "orchestrator_rules",
            }
        )

    total_cases = len(WORKFLOW_EVALS)

    return {
        "score": _workflow_score_dict(passed_cases=passed_cases, total_cases=total_cases),
        "passed_cases": passed_cases,
        "total_cases": total_cases,
        "runs": runs,
        "reflections": reflections,
        "safety": safety_metrics(runs),
        "eval_mode": "orchestrator_rules",
    }


def _scenario_public_dict(scenario: Scenario) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "query": scenario.query,
        "category": scenario.category,
        "expected_contains": list(scenario.expected_contains),
        "expected_tools": list(scenario.expected_tools),
        "expected_evidence_ids": list(scenario.expected_evidence_ids),
        "forbidden_contains": list(scenario.forbidden_contains),
    }


def _run_workflow_evals_openai() -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    passed_cases = 0

    with _temp_env("EMAIL_CALENDAR_AGENT_BACKEND", "openai"):
        harness = HarnessCore(BASELINE_CONFIG)
        for scenario in WORKFLOW_OPENAI_SCENARIOS:
            result = harness.execute(scenario)
            ok = result.run.passed
            if ok:
                passed_cases += 1

            tool_calls_serializable = [
                {
                    "tool": call.tool,
                    "args": dict(call.args),
                    "result_count": call.result_count,
                    "evidence_ids": list(call.evidence_ids),
                }
                for call in result.run.tool_calls
            ]

            runs.append(
                {
                    "case": _scenario_public_dict(scenario),
                    "passed": ok,
                    "failure_reason": result.run.failure_reason,
                    "answer_preview": result.run.answer[:600],
                    "tool_calls": tool_calls_serializable,
                    "evaluator_decision": result.evaluator_decision,
                    "eval_mode": "openai_tools",
                }
            )

    total_cases = len(WORKFLOW_OPENAI_SCENARIOS)

    return {
        "score": _workflow_score_dict(passed_cases=passed_cases, total_cases=total_cases),
        "passed_cases": passed_cases,
        "total_cases": total_cases,
        "runs": runs,
        "reflections": [],
        "safety": safety_metrics(runs),
        "eval_mode": "openai_tools",
    }
