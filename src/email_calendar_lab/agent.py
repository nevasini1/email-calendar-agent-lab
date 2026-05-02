from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from datetime import timedelta

from .fixtures import NOW
from .models import AgentRun, Scenario
from .tool_broker import ToolBroker
from .tools import CalendarTools, GmailTools, display_person, resolve_contacts


@dataclass(frozen=True)
class AgentConfig:
    name: str
    prompt_rules: tuple[str, ...]
    model: str = "gpt-5.4-mini"

    def has(self, rule: str) -> bool:
        return rule in self.prompt_rules


BASELINE_CONFIG = AgentConfig(
    name="weak-baseline",
    prompt_rules=("minimal_tool_use",),
)


class DeterministicEmailCalendarPolicy:
    """Reproducible domain behavior behind the provider/harness boundary."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def answer(self, scenario: Scenario, broker: ToolBroker) -> str:
        gmail = broker.gmail
        calendar = broker.calendar
        q = scenario.query.lower()

        if self.config.has("answer_fast_without_new_evidence"):
            return "I answered quickly without checking email or calendar evidence."

        if "free time" in q and "alex" in q:
            answer = self._answer_free_time(q, calendar)
        elif ("flight" in q or "fly" in q) and ("where" in q or "city" in q):
            answer = self._answer_flight_destination(gmail)
        elif "flight" in q and "arrive" in q:
            answer = self._answer_flight_arrival(gmail)
        elif "last sync" in q:
            answer = self._answer_last_sync(gmail)
        elif "sarah" in q and "before" in q and "offsite" in q:
            answer = self._answer_sarah_before_offsite(calendar)
        elif "sarah" in q and "last meet" in q:
            answer = self._answer_last_meeting_with_sarah(calendar)
        elif "last recurring" in q:
            answer = self._answer_last_recurring(calendar)
        elif "next" in q and ("meeting" in q or "calendar" in q):
            answer = self._answer_next_meeting(calendar)
        else:
            answer = "I could not answer from the available tools."
        return answer

    def _answer_next_meeting(self, calendar: CalendarTools) -> str:
        include_cancelled = not self.config.has("exclude_cancelled_events")
        events = calendar.search_events(time_min=NOW, include_cancelled=include_cancelled)
        if not events:
            return "No upcoming meetings found."
        event = events[0]
        return f"Your next meeting is {event.title} at {event.start.strftime('%-I:%M %p')}."

    def _answer_last_sync(self, gmail: GmailTools) -> str:
        emails = gmail.search_emails("sync")
        if not emails:
            return "I could not find a recent sync."
        email = emails[0]
        person = email.sender if not self.config.has("prefer_human_participants") else display_person(email.sender)
        return f"You last synced with {person}."

    def _answer_flight_destination(self, gmail: GmailTools) -> str:
        emails = gmail.search_emails("flight")
        if not emails:
            return "I could not find a recent flight."
        email = emails[0]
        if self.config.has("parse_flight_destination"):
            match = re.search(r"arrives?\s+([A-Z]{3})|to\s+([A-Z]{3})", email.body + " " + email.subject)
            destination = next(group for group in match.groups() if group) if match else "unknown"
            return f"Your last flight was to {destination}."
        match = re.search(r"departs?\s+([A-Z]{3})|([A-Z]{3})\s+to", email.body + " " + email.subject)
        origin = next(group for group in match.groups() if group) if match else "unknown"
        return f"Your last flight was from {origin}."

    def _answer_flight_arrival(self, gmail: GmailTools) -> str:
        emails = gmail.search_emails("flight")
        if not emails:
            return "I could not find a recent flight."
        email = emails[0]
        if self.config.has("preserve_source_timezones"):
            match = re.search(r"arrives\s+[A-Z]{3}\s+at\s+([0-9:]+\s+[AP]M\s+PT)", email.body)
            return f"Your last flight arrived at {match.group(1)}." if match else "I found the flight but not arrival time."
        return "Your last flight arrived at 2:35 PM ET."

    def _answer_free_time(self, q: str, calendar: CalendarTools) -> str:
        contacts = resolve_contacts("alex")
        start = NOW + timedelta(days=(7 - NOW.weekday()))
        end = start + timedelta(days=5)
        if self.config.has("clarify_ambiguous_contacts") and len(contacts) > 1:
            # Still call free/busy to show the tool path the agent would use after clarification.
            for contact in contacts:
                calendar.free_busy(contact.email, start, end)
            names = ", ".join(contact.name for contact in contacts)
            return f"Which Alex do you mean: {names}?"
        chosen = contacts[-1]
        calendar.free_busy(chosen.email, start, end)
        return f"You and {chosen.name} are free Tuesday at 10:00 AM."

    def _answer_sarah_before_offsite(self, calendar: CalendarTools) -> str:
        sarah = resolve_contacts("sarah")[0]
        if self.config.has("respect_temporal_anchors"):
            offsite = calendar.search_events("offsite", time_min=NOW)[0]
            events = calendar.search_events(attendee=sarah.email, time_max=offsite.start, include_cancelled=False)
            event = events[-1]
            return f"You last met Sarah before the offsite at {event.title} on {event.start.strftime('%b %-d')}."
        events = calendar.search_events(attendee=sarah.email, include_cancelled=True)
        event = events[-1]
        return f"You last met Sarah at {event.title} on {event.start.strftime('%b %-d')}."

    def _answer_last_meeting_with_sarah(self, calendar: CalendarTools) -> str:
        sarah = resolve_contacts("sarah")[0]
        events = calendar.search_events(attendee=sarah.email, time_max=NOW, include_cancelled=False)
        if not events:
            return "I could not find a Sarah meeting."
        event = events[-1]
        return f"You last met Sarah at {event.title} on {event.start.strftime('%b %-d')}."

    def _answer_last_recurring(self, calendar: CalendarTools) -> str:
        events = calendar.search_events("Team standup", time_max=NOW)
        if not events:
            return "I could not find a recurring team meeting."
        event = events[-1]
        return f"Your last recurring team meeting was {event.title} on {event.start.strftime('%b %-d')}."


class EmailCalendarAgent:
    """Backward-compatible facade over the deterministic policy."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def answer(self, scenario: Scenario) -> AgentRun:
        broker = ToolBroker()
        answer = DeterministicEmailCalendarPolicy(self.config).answer(scenario, broker)
        passed, reason, root_cause = score_answer(answer, broker.calls, scenario)
        return AgentRun(
            scenario_id=scenario.id,
            query=scenario.query,
            answer=answer,
            tool_calls=broker.calls,
            passed=passed,
            failure_reason=reason,
            root_cause=root_cause,
        )


def score_answer(answer: str, tool_calls, scenario: Scenario) -> tuple[bool, str | None, str | None]:
    missing = [needle for needle in scenario.expected_contains if needle.lower() not in answer.lower()]
    forbidden = [needle for needle in scenario.forbidden_contains if needle.lower() in answer.lower()]
    used_tools = {call.tool for call in tool_calls}
    missing_tools = [tool for tool in scenario.expected_tools if tool not in used_tools]
    evidence_ids = {evidence_id for call in tool_calls for evidence_id in call.evidence_ids}
    missing_evidence = [evidence_id for evidence_id in scenario.expected_evidence_ids if evidence_id not in evidence_ids]
    missing_args = _missing_required_tool_args(tool_calls, scenario.required_tool_args)
    if forbidden:
        return False, f"forbidden answer content present: {forbidden}", scenario.category
    if missing:
        return False, f"missing expected answer content: {missing}", scenario.category
    if missing_tools:
        return False, f"missing expected tool calls: {missing_tools}", scenario.category
    if missing_evidence:
        return False, f"missing expected evidence ids: {missing_evidence}", scenario.category
    if missing_args:
        return False, f"missing required tool args: {missing_args}", scenario.category
    return True, None, None


def _missing_required_tool_args(tool_calls, required_tool_args: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    missing: dict[str, dict[str, Any]] = {}
    for tool, expected_args in required_tool_args.items():
        matching_calls = [call for call in tool_calls if call.tool == tool]
        for key, expected_value in expected_args.items():
            if any(call.args.get(key) == expected_value for call in matching_calls):
                continue
            missing.setdefault(tool, {})[key] = expected_value
    return missing
