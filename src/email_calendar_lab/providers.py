from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PromptBundle:
    model: str
    provider: str
    system_prompt: str
    rules: tuple[str, ...]
    tool_schemas: tuple[str, ...]
    skill_ids: tuple[str, ...] = ()
    skill_summaries: tuple[str, ...] = ()


class ModelProvider(Protocol):
    name: str
    model: str

    def prompt_bundle(
        self,
        agent_name: str,
        rules: tuple[str, ...],
        tool_schemas: tuple[str, ...],
        skill_ids: tuple[str, ...] = (),
        skill_summaries: tuple[str, ...] = (),
    ) -> PromptBundle:
        """Build the provider-neutral prompt bundle for a session."""


@dataclass(frozen=True)
class DeterministicProvider:
    model: str = "gpt-5.4-mini"
    name: str = "deterministic-local"

    def prompt_bundle(
        self,
        agent_name: str,
        rules: tuple[str, ...],
        tool_schemas: tuple[str, ...],
        skill_ids: tuple[str, ...] = (),
        skill_summaries: tuple[str, ...] = (),
    ) -> PromptBundle:
        return PromptBundle(
            model=self.model,
            provider=self.name,
            system_prompt=(
                f"{agent_name} answers email/calendar questions using tools, "
                "then returns only facts supported by tool evidence."
            ),
            rules=rules,
            tool_schemas=tool_schemas,
            skill_ids=skill_ids,
            skill_summaries=skill_summaries,
        )

