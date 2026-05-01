from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from .models import AuditEvent, CalendarMutation, DraftEmail, SafetyDecision


class SafetyGate:
    """Converts every write-capable action into a dry-run decision by default."""

    def __init__(self, mode: str = "dry_run") -> None:
        self.mode = mode
        self.audit_events: list[AuditEvent] = []

    def review_draft(self, draft: DraftEmail) -> SafetyDecision:
        return self._record("email_draft", draft.id, draft.evidence_ids)

    def review_calendar_mutation(self, mutation: CalendarMutation) -> SafetyDecision:
        return self._record(f"calendar_{mutation.operation}", mutation.id, mutation.evidence_ids)

    def _record(self, action_type: str, action_id: str, evidence_ids: tuple[str, ...]) -> SafetyDecision:
        decision = SafetyDecision(
            allowed=self.mode == "confirmed",
            mode="confirmed" if self.mode == "confirmed" else "dry_run",
            requires_confirmation=self.mode != "confirmed",
            reason="Dry-run only until the user explicitly confirms the action."
            if self.mode != "confirmed"
            else "Action explicitly confirmed.",
        )
        self.audit_events.append(
            AuditEvent(
                id=f"audit_{len(self.audit_events) + 1}_{action_id}",
                action_type=action_type,
                action_id=action_id,
                decision=decision,
                created_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                evidence_ids=evidence_ids,
            )
        )
        return decision

    def to_dicts(self) -> list[dict]:
        return [asdict(event) for event in self.audit_events]
