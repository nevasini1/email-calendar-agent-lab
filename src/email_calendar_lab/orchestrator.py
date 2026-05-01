from __future__ import annotations

from .calendar_agent import CalendarAgent
from .email_agent import EmailAgent
from .fixtures import ALL_EVENTS, EMAILS, NOW
from .workflow_agent import WorkflowAgent


class Orchestrator:
    def __init__(self, emails=EMAILS, events=ALL_EVENTS, now=NOW) -> None:
        self.email_agent = EmailAgent(emails)
        self.calendar_agent = CalendarAgent(events, now)
        self.workflow_agent = WorkflowAgent(self.email_agent, self.calendar_agent)

    def route(self, query: str):
        lowered = query.lower()
        if "priority inbox" in lowered:
            return self.workflow_agent.priority_inbox_summary()
        if "meeting request" in lowered or "launch plan" in lowered:
            email = next(email for email in self.email_agent.emails if email.id == "mail_meeting_request_maya")
            return self.workflow_agent.meeting_request_to_invite(email)
        if "cancel" in lowered or "cancellation" in lowered:
            email = next(email for email in self.email_agent.emails if email.id == "mail_cancellation_ops")
            return self.workflow_agent.cancellation_to_update(email)
        if "weekly review" in lowered:
            return self.workflow_agent.weekly_review()
        return self.email_agent.priority_inbox()
