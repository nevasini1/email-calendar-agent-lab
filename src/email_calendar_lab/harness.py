from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from .agent import AgentConfig, DeterministicEmailCalendarPolicy, score_answer
from .models import AgentRun, Scenario
from .providers import DeterministicProvider, ModelProvider, OpenAILiveProvider, PromptBundle
from .skills import SkillLibrary
from .tool_broker import ToolBroker

AgentMode = Literal["plan", "build"]


@dataclass(frozen=True)
class ToolRequest:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class SessionStep:
    kind: Literal["prompt", "tool", "answer", "eval"]
    payload: dict[str, Any]


@dataclass
class Session:
    id: str
    scenario_id: str
    mode: AgentMode
    provider: str
    model: str
    prompt_bundle: PromptBundle
    started_at: str
    steps: list[SessionStep] = field(default_factory=list)


@dataclass
class HarnessResult:
    run: AgentRun
    session: Session
    tool_trace: list[dict[str, Any]]
    evaluator_decision: dict[str, Any]


class HarnessCore:
    """Core agent/session runner, separated from CLI and eval orchestration."""

    def __init__(self, config: AgentConfig, provider: ModelProvider | None = None) -> None:
        backend = os.environ.get("EMAIL_CALENDAR_AGENT_BACKEND", "deterministic").lower().strip()
        if backend == "openai":
            lm = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
            config = replace(config, model=lm)
        self._backend = backend
        self.config = config
        self.provider = provider or (OpenAILiveProvider(model=config.model) if backend == "openai" else DeterministicProvider(model=config.model))

    def execute(self, scenario: Scenario, mode: AgentMode = "build") -> HarnessResult:
        broker = ToolBroker()
        skills = SkillLibrary().match(scenario.category, scenario.query)
        prompt_bundle = self.provider.prompt_bundle(
            self.config.name,
            self.config.prompt_rules,
            broker.schema_names(),
            tuple(skill.id for skill in skills),
            tuple(skill.to_prompt_summary() for skill in skills),
        )
        session = Session(
            id=f"{scenario.id}-{uuid4().hex[:8]}",
            scenario_id=scenario.id,
            mode=mode,
            provider=prompt_bundle.provider,
            model=prompt_bundle.model,
            prompt_bundle=prompt_bundle,
            started_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
        session.steps.append(SessionStep("prompt", asdict(prompt_bundle)))

        if mode == "plan":
            answer = self._plan_answer(scenario, broker)
        elif self._backend == "openai":
            from .openai_llm_agent import answer_with_openai

            answer = answer_with_openai(scenario, broker, self.config, model=self.config.model)
        else:
            answer = DeterministicEmailCalendarPolicy(self.config).answer(scenario, broker)

        for call in broker.calls:
            session.steps.append(SessionStep("tool", asdict(call)))

        passed, reason, root_cause = score_answer(answer, broker.calls, scenario)
        run = AgentRun(
            scenario_id=scenario.id,
            query=scenario.query,
            answer=answer,
            tool_calls=broker.calls,
            passed=passed,
            failure_reason=reason,
            root_cause=root_cause,
        )
        session.steps.append(SessionStep("answer", {"answer": answer}))
        evaluator_decision = {
            "passed": passed,
            "failure_reason": reason,
            "root_cause": root_cause,
            "expected_evidence_ids": scenario.expected_evidence_ids,
            "loaded_skill_ids": prompt_bundle.skill_ids,
        }
        session.steps.append(SessionStep("eval", evaluator_decision))
        return HarnessResult(run=run, session=session, tool_trace=broker.trace(), evaluator_decision=evaluator_decision)

    def _plan_answer(self, scenario: Scenario, broker: ToolBroker) -> str:
        expected_tools = ", ".join(scenario.expected_tools or broker.schema_names())
        return f"Plan mode only: inspect scenario '{scenario.id}' and consider tools: {expected_tools}."

    def answer(self, scenario: Scenario) -> AgentRun:
        return self.execute(scenario, mode="build").run
