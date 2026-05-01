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
    calendar_id: str = "work"
    owner: str = "me@example.com"
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    travel_minutes: int = 0
    recurrence_rule: str | None = None
    conflict_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailAttachment:
    id: str
    filename: str
    content_type: str
    text: str


@dataclass(frozen=True)
class Email:
    id: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    sent_at: datetime
    body: str
    thread_id: str | None = None
    attachments: tuple[EmailAttachment, ...] = ()
    labels: tuple[str, ...] = ()
    importance: int = 0
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    action_items: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class DraftEmail:
    id: str
    to: tuple[str, ...]
    subject: str
    body: str
    thread_id: str | None = None
    requires_confirmation: bool = True
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarMutation:
    id: str
    operation: Literal["create", "update", "cancel", "reschedule"]
    title: str
    start: datetime | None = None
    end: datetime | None = None
    attendees: tuple[str, ...] = ()
    calendar_id: str = "work"
    event_id: str | None = None
    requires_confirmation: bool = True
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    mode: Literal["read_only", "dry_run", "confirmed"] = "dry_run"
    requires_confirmation: bool = True
    reason: str = "Side effects require explicit confirmation."


@dataclass(frozen=True)
class AuditEvent:
    id: str
    action_type: str
    action_id: str
    decision: SafetyDecision
    created_at: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowPlan:
    id: str
    workflow_type: str
    summary: str
    drafts: tuple[DraftEmail, ...] = ()
    calendar_mutations: tuple[CalendarMutation, ...] = ()
    safety_decisions: tuple[SafetyDecision, ...] = ()
    audit_events: tuple[AuditEvent, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    risk_level: Literal["low", "medium", "high"] = "medium"
    requires_confirmation: bool = True

