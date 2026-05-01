from __future__ import annotations

from .calendar_agent import CalendarAgent
from .email_agent import EmailAgent
from .models import DraftEmail, Email, WorkflowPlan
from .safety import SafetyGate


class WorkflowAgent:
    def __init__(self, email_agent: EmailAgent, calendar_agent: CalendarAgent, safety_gate: SafetyGate | None = None) -> None:
        self.email_agent = email_agent
        self.calendar_agent = calendar_agent
        self.safety_gate = safety_gate or SafetyGate()

    def priority_inbox_summary(self) -> WorkflowPlan:
        items = self.email_agent.priority_inbox()
        summary = "; ".join(f"{item.subject} from {item.sender} ({', '.join(item.reasons)})" for item in items)
        evidence_ids = tuple(item.email_id for item in items)
        return WorkflowPlan(
            id="workflow_priority_inbox",
            workflow_type="priority_inbox",
            summary=f"Priority inbox: {summary}",
            evidence_ids=evidence_ids,
            risk_level="low",
            requires_confirmation=False,
        )

    def meeting_request_to_invite(self, email: Email) -> WorkflowPlan:
        safety_gate = self._workflow_safety_gate()
        slots = self.calendar_agent.suggest_smart_slots(email.sent_at.replace(day=5))
        slot = slots[0]
        mutation = self.calendar_agent.propose_create(
            "Launch plan with Maya",
            slot.start,
            30,
            (email.sender,),
            (email.id,),
        )
        draft = DraftEmail(
            id="draft_invite_maya",
            to=(email.sender,),
            subject=f"Re: {email.subject}",
            body=f"I found {slot.start.strftime('%a %b %-d at %-I:%M %p %Z')} for 30 minutes and drafted a calendar invite for confirmation.",
            thread_id=email.thread_id,
            evidence_ids=(email.id, mutation.id),
        )
        decisions = (safety_gate.review_calendar_mutation(mutation), safety_gate.review_draft(draft))
        return WorkflowPlan(
            id="workflow_meeting_request_maya",
            workflow_type="meeting_request",
            summary="Parsed Maya's meeting request, found a conflict-free slot, and drafted an invite response.",
            drafts=(draft,),
            calendar_mutations=(mutation,),
            safety_decisions=decisions,
            audit_events=tuple(safety_gate.audit_events),
            evidence_ids=(email.id,),
            risk_level="medium",
        )

    def cancellation_to_update(self, email: Email) -> WorkflowPlan:
        safety_gate = self._workflow_safety_gate()
        mutation = self.calendar_agent.propose_cancel("evt_ops_review", (email.id, "evt_ops_review"))
        draft = DraftEmail(
            id="draft_ops_cancel_notice",
            to=("dana@example.com",),
            subject="Ops review cancellation",
            body="Jordan asked to cancel today's Ops review. I drafted the calendar cancellation and this participant notice for confirmation.",
            thread_id=email.thread_id,
            evidence_ids=(email.id, mutation.event_id or ""),
        )
        decisions = (safety_gate.review_calendar_mutation(mutation), safety_gate.review_draft(draft))
        return WorkflowPlan(
            id="workflow_cancellation_ops",
            workflow_type="cancellation",
            summary="Detected the cancellation request, proposed cancelling Ops review, and drafted a notification.",
            drafts=(draft,),
            calendar_mutations=(mutation,),
            safety_decisions=decisions,
            audit_events=tuple(safety_gate.audit_events),
            evidence_ids=(email.id, "evt_ops_review"),
            risk_level="high",
        )

    def weekly_review(self) -> WorkflowPlan:
        priority = self.priority_inbox_summary()
        conflicts = self.calendar_agent.recurrence_conflicts()
        summary = f"Weekly review includes {len(priority.evidence_ids)} priority emails and {len(conflicts)} recurrence conflicts."
        return WorkflowPlan(
            id="workflow_weekly_review",
            workflow_type="weekly_review",
            summary=summary,
            evidence_ids=priority.evidence_ids + tuple(event.id for event in conflicts),
            risk_level="low",
            requires_confirmation=False,
        )

    def _workflow_safety_gate(self) -> SafetyGate:
        return SafetyGate(self.safety_gate.mode)
