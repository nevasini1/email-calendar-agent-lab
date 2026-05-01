from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import ToolCall
from .tools import CalendarTools, GmailTools, ToolRecorder


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class ToolResult:
    tool: str
    args: dict[str, Any]
    result_count: int
    evidence_ids: tuple[str, ...]


class ToolBroker:
    """Mediates mocked tools and exposes opencode-style tool traces."""

    def __init__(self) -> None:
        self.recorder = ToolRecorder()
        self.gmail = GmailTools(self.recorder)
        self.calendar = CalendarTools(self.recorder)
        self.schemas = (
            ToolSchema("gmail.search_emails", "Search mocked Gmail messages.", ("query", "after", "before")),
            ToolSchema(
                "calendar.search_events",
                "Search mocked calendar events.",
                ("query", "time_min", "time_max", "attendee", "include_cancelled"),
            ),
            ToolSchema("calendar.free_busy", "Return busy events for an attendee.", ("attendee", "start", "end")),
        )

    @property
    def calls(self) -> list[ToolCall]:
        return self.recorder.calls

    def schema_names(self) -> tuple[str, ...]:
        return tuple(schema.name for schema in self.schemas)

    def trace(self) -> list[dict[str, Any]]:
        return [asdict(ToolResult(call.tool, call.args, call.result_count, call.evidence_ids)) for call in self.calls]

