from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import CalendarEvent, CalendarMutation


@dataclass(frozen=True)
class SmartSlot:
    start: datetime
    end: datetime
    calendar_id: str
    reasons: tuple[str, ...]


class CalendarAgent:
    def __init__(self, events: tuple[CalendarEvent, ...], now: datetime) -> None:
        self.events = events
        self.now = now

    def multi_calendar_availability(
        self,
        start: datetime,
        end: datetime,
        calendar_ids: tuple[str, ...] = ("work",),
    ) -> dict[str, list[CalendarEvent]]:
        busy: dict[str, list[CalendarEvent]] = {calendar_id: [] for calendar_id in calendar_ids}
        for event in self.events:
            if event.status == "cancelled" or event.calendar_id not in busy:
                continue
            buffered_start = event.start - timedelta(minutes=event.buffer_before_minutes + event.travel_minutes)
            buffered_end = event.end + timedelta(minutes=event.buffer_after_minutes)
            if buffered_start < end and buffered_end > start:
                busy[event.calendar_id].append(event)
        return busy

    def suggest_smart_slots(
        self,
        day: datetime,
        duration_minutes: int = 30,
        calendar_ids: tuple[str, ...] = ("work",),
    ) -> tuple[SmartSlot, ...]:
        slots: list[SmartSlot] = []
        cursor = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end_of_day = day.replace(hour=17, minute=0, second=0, microsecond=0)
        while cursor + timedelta(minutes=duration_minutes) <= end_of_day:
            slot_end = cursor + timedelta(minutes=duration_minutes)
            busy = self.multi_calendar_availability(cursor, slot_end, calendar_ids)
            if not any(busy.values()):
                slots.append(SmartSlot(cursor, slot_end, calendar_ids[0], ("working_hours", "no_conflicts")))
            cursor += timedelta(minutes=30)
        return tuple(slots)

    def recurrence_conflicts(self) -> tuple[CalendarEvent, ...]:
        recurring = [event for event in self.events if event.recurrence_id and event.status == "confirmed"]
        conflicts: list[CalendarEvent] = []
        for event in recurring:
            if any(other.id != event.id and other.status == "confirmed" and other.start < event.end and other.end > event.start for other in self.events):
                conflicts.append(event)
        return tuple(conflicts)

    def propose_create(self, title: str, start: datetime, minutes: int, attendees: tuple[str, ...], evidence_ids: tuple[str, ...]) -> CalendarMutation:
        return CalendarMutation(
            id=f"cal_create_{title.lower().replace(' ', '_')}",
            operation="create",
            title=title,
            start=start,
            end=start + timedelta(minutes=minutes),
            attendees=attendees,
            evidence_ids=evidence_ids,
        )

    def propose_cancel(self, event_id: str, evidence_ids: tuple[str, ...]) -> CalendarMutation:
        event = next(event for event in self.events if event.id == event_id)
        return CalendarMutation(
            id=f"cal_cancel_{event_id}",
            operation="cancel",
            title=event.title,
            event_id=event.id,
            start=event.start,
            end=event.end,
            attendees=event.attendees,
            calendar_id=event.calendar_id,
            evidence_ids=evidence_ids,
        )
