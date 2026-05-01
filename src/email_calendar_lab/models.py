from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class Contact:
    name: str
    email: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: tuple[str, ...]
    location: str | None = None
    status: Literal["confirmed", "cancelled"] = "confirmed"
    recurrence_id: str | None = None


@dataclass(frozen=True)
class Email:
    id: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    sent_at: datetime
    body: str


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    result_count: int
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    query: str
    expected_contains: tuple[str, ...]
    category: str
    expected_tools: tuple[str, ...] = ()
    split: Literal["production", "stable", "heldout"] = "production"
    expected_evidence_ids: tuple[str, ...] = ()
    forbidden_contains: tuple[str, ...] = ()
    required_tool_args: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AgentRun:
    scenario_id: str
    query: str
    answer: str
    tool_calls: list[ToolCall]
    passed: bool
    failure_reason: str | None = None
    root_cause: str | None = None


@dataclass
class EvalCase:
    id: str
    query: str
    expected_contains: tuple[str, ...]
    category: str
    source_failure: str
    expected_tools: tuple[str, ...] = field(default_factory=tuple)
    expected_evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    forbidden_contains: tuple[str, ...] = field(default_factory=tuple)
    required_tool_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    lifecycle: Literal["candidate", "stable", "heldout"] = "candidate"
    origin_run_id: str | None = None
    root_cause: str | None = None
    reflection_id: str | None = None
    lesson_type: str | None = None
    promotion_status: Literal["quarantined", "promoted", "rejected"] = "quarantined"
    first_seen_at: str | None = None
    seen_count: int = 1

