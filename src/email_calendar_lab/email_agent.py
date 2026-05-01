from __future__ import annotations

import re
from dataclasses import dataclass

from .models import DraftEmail, Email


@dataclass(frozen=True)
class PriorityInboxItem:
    email_id: str
    subject: str
    sender: str
    score: int
    reasons: tuple[str, ...]
    escalation: bool = False


class EmailAgent:
    def __init__(self, emails: tuple[Email, ...]) -> None:
        self.emails = emails

    def priority_inbox(self, limit: int = 5) -> tuple[PriorityInboxItem, ...]:
        items = [self._priority_item(email) for email in self.emails if "inbox" in email.labels]
        return tuple(sorted(items, key=lambda item: item.score, reverse=True)[:limit])

    def summarize_thread(self, thread_id: str) -> str:
        thread = sorted((email for email in self.emails if email.thread_id == thread_id), key=lambda email: email.sent_at)
        if not thread:
            return "No matching thread found."
        subjects = ", ".join(email.subject for email in thread)
        action_items = sorted({item for email in thread for item in email.action_items})
        suffix = f" Action items: {', '.join(action_items)}." if action_items else ""
        return f"{len(thread)} message thread covering: {subjects}.{suffix}"

    def extract_attachment_dates(self, email_id: str) -> tuple[str, ...]:
        email = self._email(email_id)
        if not email:
            return ()
        matches: list[str] = []
        pattern = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M [A-Z]{2}", re.I)
        for attachment in email.attachments:
            matches.extend(pattern.findall(attachment.text))
        return tuple(matches)

    def draft_escalation_reply(self, email: Email) -> DraftEmail:
        return DraftEmail(
            id=f"draft_escalation_{email.id}",
            to=(email.sender,),
            subject=f"Re: {email.subject}",
            body="Thanks for flagging this. I will escalate it today and send an update once the owner confirms next steps.",
            thread_id=email.thread_id,
            evidence_ids=(email.id,),
        )

    def _priority_item(self, email: Email) -> PriorityInboxItem:
        reasons: list[str] = []
        score = email.importance
        if "action_required" in email.labels:
            score += 4
            reasons.append("action_required")
        if email.sentiment == "negative":
            score += 3
            reasons.append("negative_sentiment")
        if email.attachments:
            score += 1
            reasons.append("attachment")
        if email.action_items:
            score += 2
            reasons.append("action_items")
        return PriorityInboxItem(email.id, email.subject, email.sender, score, tuple(reasons), email.sentiment == "negative")

    def _email(self, email_id: str) -> Email | None:
        return next((email for email in self.emails if email.id == email_id), None)
