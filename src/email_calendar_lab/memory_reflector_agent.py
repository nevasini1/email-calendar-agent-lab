from __future__ import annotations

from dataclasses import asdict

from .models import WorkflowPlan


class MemoryReflectorAgent:
    def reflect_workflow_plan(self, plan: WorkflowPlan, passed: bool, reason: str | None = None) -> dict:
        lesson_type = "workflow_success" if passed else "workflow_failure"
        return {
            "id": f"workflow_reflection_{plan.id}",
            "scenario_id": plan.id,
            "lesson_type": lesson_type,
            "generalizes": not passed or plan.workflow_type in {"meeting_request", "cancellation"},
            "recommended_artifact": "eval" if not passed else "skill",
            "summary": plan.summary if passed else reason or "Workflow failed deterministic checks.",
            "workflow_plan": asdict(plan),
        }
