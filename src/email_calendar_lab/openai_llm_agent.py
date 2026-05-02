from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from openai import OpenAI

from .agent import AgentConfig
from .fixtures import CONTACTS, NOW
from .models import CalendarEvent, Email, Scenario
from .tool_broker import ToolBroker

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "gmail_search_emails",
            "description": "Search mocked Gmail by keywords in sender, subject, and body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Space-separated keywords to match."},
                    "after": {"type": ["string", "null"], "description": "ISO datetime lower bound (exclusive)."},
                    "before": {"type": ["string", "null"], "description": "ISO datetime upper bound (exclusive)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_search_events",
            "description": "Search calendar events by title/location text, time window, attendee email, cancellation flag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": ["string", "null"], "description": "Substring match on title or location."},
                    "time_min": {"type": ["string", "null"], "description": "ISO datetime; events starting before this are excluded."},
                    "time_max": {"type": ["string", "null"], "description": "ISO datetime; events starting at or after this are excluded."},
                    "attendee": {"type": ["string", "null"], "description": "Attendee email must be on the event."},
                    "include_cancelled": {
                        "type": "boolean",
                        "description": "Whether to include cancelled instances.",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_free_busy",
            "description": "Return busy intervals for one attendee between start and end.",
            "parameters": {
                "type": "object",
                "properties": {
                    "attendee": {"type": "string"},
                    "start": {"type": "string", "description": "ISO datetime inclusive."},
                    "end": {"type": "string", "description": "ISO datetime exclusive."},
                },
                "required": ["attendee", "start", "end"],
            },
        },
    },
]


def _client_timeout_seconds() -> float:
    raw = os.environ.get("OPENAI_CLIENT_TIMEOUT_SEC", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 30.0
    return min(value, 180.0)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _email_blob(email: Email) -> dict[str, Any]:
    return {
        "id": email.id,
        "sender": email.sender,
        "subject": email.subject,
        "sent_at": email.sent_at.isoformat(),
        "body": email.body[:1200],
    }


def _event_blob(ev: CalendarEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "title": ev.title,
        "start": ev.start.isoformat(),
        "end": ev.end.isoformat(),
        "attendees": list(ev.attendees),
        "status": ev.status,
        "location": ev.location,
    }


def _contacts_catalog() -> str:
    lines = [f"- {c.name} <{c.email}> aliases={list(c.aliases)}" for c in CONTACTS]
    return "\n".join(lines)


def _dispatch_tool(broker: ToolBroker, fn: str, raw_args: dict[str, Any]) -> str:
    gmail = broker.gmail
    calendar = broker.calendar
    if fn == "gmail_search_emails":
        q = str(raw_args.get("query", ""))
        after = _parse_dt(raw_args.get("after"))
        before = _parse_dt(raw_args.get("before"))
        emails = gmail.search_emails(q, after=after, before=before)
        payload = {"emails": [_email_blob(e) for e in emails[:12]]}
        return json.dumps(payload, default=str)
    if fn == "calendar_search_events":
        events = calendar.search_events(
            query=raw_args.get("query"),
            time_min=_parse_dt(raw_args.get("time_min")),
            time_max=_parse_dt(raw_args.get("time_max")),
            attendee=raw_args.get("attendee"),
            include_cancelled=_coerce_bool(raw_args.get("include_cancelled"), False),
        )
        payload = {"events": [_event_blob(e) for e in events[:40]]}
        return json.dumps(payload, default=str)
    if fn == "calendar_free_busy":
        attendee = str(raw_args.get("attendee", ""))
        start = _parse_dt(raw_args.get("start"))
        end = _parse_dt(raw_args.get("end"))
        if start is None or end is None:
            return json.dumps({"error": "start and end must be valid ISO datetimes"})
        busy = calendar.free_busy(attendee, start, end)
        payload = {"busy_events": [_event_blob(e) for e in busy[:40]]}
        return json.dumps(payload, default=str)
    return json.dumps({"error": f"unknown tool {fn}"})


def answer_with_openai(scenario: Scenario, broker: ToolBroker, config: AgentConfig, *, model: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when EMAIL_CALENDAR_AGENT_BACKEND=openai")

    client = OpenAI(api_key=api_key, timeout=_client_timeout_seconds())
    system = (
        f"You are {config.name}. Current time anchor (user TZ): {NOW.isoformat()}. "
        "Answer the user's question using only facts from tool results. "
        "Call tools as needed; prefer narrow searches. "
        "When the user refers to people by first name, resolve emails using this directory:\n"
        f"{_contacts_catalog()}\n"
        "After tools return, reply with a short natural-language answer. "
        "Do not invent IDs, times, or attendees not present in tool output."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": scenario.query},
    ]

    max_rounds = int(os.environ.get("OPENAI_AGENT_MAX_ROUNDS", "14"))
    for _ in range(max_rounds):
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": _TOOL_DEFS,
            "tool_choice": "auto",
        }
        try:
            resp = client.chat.completions.create(**create_kwargs, max_completion_tokens=2048)
        except TypeError:
            resp = client.chat.completions.create(**create_kwargs, max_tokens=2048)
        except Exception:
            return "I could not complete the model call. Please retry."

        choice = resp.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None) or []
        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _dispatch_tool(broker, name, args if isinstance(args, dict) else {})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            continue

        text = (choice.content or "").strip()
        if text:
            return text
        return "I could not produce an answer from the available tools."

    return "I stopped after the tool-call budget without a final answer."
