from __future__ import annotations

from datetime import datetime

from .fixtures import ALL_EVENTS, CONTACTS, EMAILS
from .models import CalendarEvent, Contact, Email, ToolCall


class ToolRecorder:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def record(self, tool: str, args: dict, result_count: int, evidence_ids: tuple[str, ...] = ()) -> None:
        self.calls.append(ToolCall(tool=tool, args=args, result_count=result_count, evidence_ids=evidence_ids))


class GmailTools:
    def __init__(self, recorder: ToolRecorder) -> None:
        self.recorder = recorder

    def search_emails(self, query: str, after: datetime | None = None, before: datetime | None = None) -> list[Email]:
        terms = query.lower().split()
        results = []
        for email in EMAILS:
            blob = f"{email.sender} {email.subject} {email.body}".lower()
            if all(term in blob for term in terms):
                if after and email.sent_at <= after:
                    continue
                if before and email.sent_at >= before:
                    continue
                results.append(email)
        results.sort(key=lambda email: email.sent_at, reverse=True)
        self.recorder.record(
            "gmail.search_emails",
            {"query": query, "after": str(after) if after else None, "before": str(before) if before else None},
            len(results),
            tuple(email.id for email in results),
        )
        return results


class CalendarTools:
    def __init__(self, recorder: ToolRecorder) -> None:
        self.recorder = recorder

    def search_events(
        self,
        query: str | None = None,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        attendee: str | None = None,
        include_cancelled: bool = False,
    ) -> list[CalendarEvent]:
        q = query.lower() if query else None
        results = []
        for event in ALL_EVENTS:
            if q and q not in f"{event.title} {event.location or ''}".lower():
                continue
            if attendee and attendee not in event.attendees:
                continue
            if time_min and event.start < time_min:
                continue
            if time_max and event.start >= time_max:
                continue
            if not include_cancelled and event.status == "cancelled":
                continue
            results.append(event)
        results.sort(key=lambda event: event.start)
        self.recorder.record(
            "calendar.search_events",
            {
                "query": query,
                "time_min": str(time_min) if time_min else None,
                "time_max": str(time_max) if time_max else None,
                "attendee": attendee,
                "include_cancelled": include_cancelled,
            },
            len(results),
            tuple(event.id for event in results),
        )
        return results

    def free_busy(self, attendee: str, start: datetime, end: datetime) -> list[CalendarEvent]:
        busy = self.search_events(time_min=start, time_max=end, attendee=attendee, include_cancelled=False)
        self.recorder.record(
            "calendar.free_busy",
            {"attendee": attendee, "start": str(start), "end": str(end)},
            len(busy),
            tuple(event.id for event in busy),
        )
        return busy


def resolve_contacts(name_fragment: str) -> list[Contact]:
    fragment = name_fragment.lower()
    matches = []
    for contact in CONTACTS:
        values = (contact.name.lower(), contact.email.lower(), *contact.aliases)
        if any(fragment in value for value in values):
            matches.append(contact)
    return matches


def display_person(email: str) -> str:
    for contact in CONTACTS:
        if contact.email == email:
            return contact.name
    return email

