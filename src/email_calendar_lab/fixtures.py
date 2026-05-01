from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import CalendarEvent, Contact, Email, Scenario

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 5, 1, 10, 0, tzinfo=NY)

CONTACTS = (
    Contact("Alex Chen", "alex.chen@example.com", ("alex", "alex c")),
    Contact("Alex Rivera", "alex.rivera@example.com", ("alex r",)),
    Contact("Sarah Patel", "sarah@example.com", ("sarah",)),
    Contact("Sara Park", "sara@example.com", ("sara",)),
    Contact("Jordan Lee", "jordan@example.com", ("jordan",)),
    Contact("Dana Kim", "dana@example.com", ("dana",)),
)


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0, tz=NY) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _event(
    id_: str,
    title: str,
    start: datetime,
    minutes: int,
    attendees: tuple[str, ...],
    location: str | None = None,
    status: str = "confirmed",
    recurrence_id: str | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        id=id_,
        title=title,
        start=start,
        end=start + timedelta(minutes=minutes),
        attendees=attendees,
        location=location,
        status=status,  # type: ignore[arg-type]
        recurrence_id=recurrence_id,
    )


EVENTS = (
    _event(
        "evt_cancelled_recruiting",
        "Recruiting screen",
        _dt(2026, 5, 1, 11),
        30,
        ("recruiter@example.com",),
        status="cancelled",
    ),
    _event("evt_ops_review", "Ops review", _dt(2026, 5, 1, 13), 45, ("jordan@example.com",)),
    _event("evt_offsite", "Leadership offsite", _dt(2026, 5, 2, 9), 420, ("sarah@example.com",), "Hudson Loft"),
    _event("evt_alex_1on1", "Alex Chen 1:1", _dt(2026, 5, 4, 9), 30, ("alex.chen@example.com",)),
    _event("evt_board_prep", "Board prep", _dt(2026, 5, 4, 11), 60, ("dana@example.com",)),
    _event("evt_sarah_cancelled", "Sarah sync", _dt(2026, 4, 30, 16), 30, ("sarah@example.com",), status="cancelled"),
    _event("evt_sarah_before_offsite", "Sarah roadmap review", _dt(2026, 4, 22, 14), 45, ("sarah@example.com",)),
    _event("evt_sarah_old", "Sarah budget sync", _dt(2026, 4, 9, 10), 30, ("sarah@example.com",)),
    _event("evt_alex_rivera_recent", "Alex Rivera vendor review", _dt(2026, 4, 29, 15), 30, ("alex.rivera@example.com",)),
)

RECURRING_EVENTS = tuple(
    _event(
        f"evt_standup_{day}",
        "Team standup",
        _dt(2026, 4, day, 9, 30),
        15,
        ("dana@example.com", "jordan@example.com"),
        recurrence_id="daily_standup",
    )
    for day in (27, 28, 29, 30)
)

ALL_EVENTS = EVENTS + RECURRING_EVENTS

EMAILS = (
    Email(
        "mail_sfo_flight",
        "receipts@airline.example",
        ("me@example.com",),
        "Your flight receipt: JFK to SFO",
        _dt(2026, 4, 18, 8),
        "Flight 282 departs JFK on Apr 20 at 8:10 AM ET and arrives SFO at 11:35 AM PT.",
    ),
    Email(
        "mail_seattle_flight",
        "receipts@airline.example",
        ("me@example.com",),
        "Your flight receipt: BOS to SEA",
        _dt(2026, 3, 4, 12),
        "Flight 119 departs BOS and arrives SEA for the customer summit.",
    ),
    Email(
        "mail_sync_dana",
        "dana@example.com",
        ("me@example.com",),
        "Re: sync notes",
        _dt(2026, 4, 30, 17),
        "Good sync today. Next steps are in the doc.",
    ),
    Email(
        "mail_sync_sarah",
        "assistant@sarah.example",
        ("me@example.com", "sarah@example.com"),
        "Sarah sync agenda",
        _dt(2026, 4, 29, 9),
        "Agenda for your Sarah sync. Sarah will join directly.",
    ),
)

PRODUCTION_SCENARIOS = (
    Scenario(
        "prod_next_meeting",
        "When is my next meeting?",
        ("Ops review", "1:00 PM"),
        "cancelled_events",
        ("calendar.search_events",),
        expected_evidence_ids=("evt_ops_review",),
        forbidden_contains=("Recruiting screen",),
        required_tool_args={"calendar.search_events": {"include_cancelled": False}},
    ),
    Scenario(
        "prod_last_sync",
        "Who did I last sync with?",
        ("Dana Kim",),
        "attendees_vs_senders",
        ("gmail.search_emails",),
        expected_evidence_ids=("mail_sync_dana",),
        forbidden_contains=("dana@example.com", "assistant@sarah.example"),
    ),
    Scenario(
        "prod_last_flight",
        "Where was my last flight?",
        ("SFO",),
        "flight_emails",
        ("gmail.search_emails",),
        expected_evidence_ids=("mail_sfo_flight",),
        forbidden_contains=("JFK",),
    ),
    Scenario(
        "prod_flight_arrival_timezone",
        "What time did my last flight arrive?",
        ("11:35 AM PT",),
        "time_zones",
        ("gmail.search_emails",),
        expected_evidence_ids=("mail_sfo_flight",),
        forbidden_contains=("2:35 PM ET",),
    ),
    Scenario(
        "prod_free_time_alex",
        "Find free time with Alex next week?",
        ("Which Alex", "Alex Chen", "Alex Rivera"),
        "ambiguous_contacts",
        ("calendar.free_busy",),
        forbidden_contains=("You and Alex Rivera are free",),
    ),
    Scenario(
        "prod_sarah_before_offsite",
        "When did I last meet Sarah before the offsite?",
        ("Sarah roadmap review", "Apr 22"),
        "last_before_anchor",
        ("calendar.search_events",),
        expected_evidence_ids=("evt_offsite", "evt_sarah_before_offsite"),
        forbidden_contains=("Leadership offsite on May 2", "Sarah sync on Apr 30"),
        required_tool_args={"calendar.search_events": {"include_cancelled": False}},
    ),
    Scenario(
        "prod_recurring_last_meeting",
        "What was my last recurring team meeting?",
        ("Team standup", "Apr 30"),
        "recurring_meetings",
        ("calendar.search_events",),
        expected_evidence_ids=("evt_standup_30",),
    ),
)

STABLE_EVALS = (
    Scenario(
        "stable_next_meeting_ignores_cancelled",
        "What meeting is next on my calendar?",
        ("Ops review", "1:00 PM"),
        "cancelled_events",
        ("calendar.search_events",),
        "stable",
        expected_evidence_ids=("evt_ops_review",),
        forbidden_contains=("Recruiting screen",),
        required_tool_args={"calendar.search_events": {"include_cancelled": False}},
    ),
    Scenario(
        "stable_last_flight_destination",
        "What city did I fly to most recently?",
        ("SFO",),
        "flight_emails",
        ("gmail.search_emails",),
        "stable",
        expected_evidence_ids=("mail_sfo_flight",),
        forbidden_contains=("JFK",),
    ),
    Scenario(
        "stable_last_sync_human_name",
        "Who did I last sync with?",
        ("Dana Kim",),
        "attendees_vs_senders",
        ("gmail.search_emails",),
        "stable",
        expected_evidence_ids=("mail_sync_dana",),
        forbidden_contains=("dana@example.com", "assistant@sarah.example"),
    ),
)

HELDOUT_EVALS = (
    Scenario(
        "heldout_recurring_last_meeting",
        "What was my last recurring team meeting?",
        ("Team standup", "Apr 30"),
        "recurring_meetings",
        ("calendar.search_events",),
        "heldout",
        expected_evidence_ids=("evt_standup_30",),
    ),
    Scenario(
        "heldout_timezone_flight",
        "What time did my last flight arrive?",
        ("11:35 AM PT",),
        "time_zones",
        ("gmail.search_emails",),
        "heldout",
        expected_evidence_ids=("mail_sfo_flight",),
        forbidden_contains=("2:35 PM ET",),
    ),
    Scenario(
        "heldout_sarah_not_sara",
        "When did I last meet Sarah?",
        ("Sarah roadmap review", "Apr 22"),
        "ambiguous_contacts",
        ("calendar.search_events",),
        "heldout",
        expected_evidence_ids=("evt_sarah_before_offsite",),
        forbidden_contains=("Sara Park",),
    ),
)

