from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .harness import HarnessResult
from .reflection import ReflectionRecord


class LangfuseSessionExporter:
    """Langfuse exporter for harness sessions.

    The exporter is enabled by default. If credentials are missing, it records a
    no-op status in the run summary instead of breaking local eval execution.
    """

    def __init__(self) -> None:
        _load_local_env()
        self.enabled = os.getenv("LANGFUSE_TRACING_ENABLED", "true").lower() not in {"0", "false", "no"}
        self.reason: str | None = None
        self.client: Any | None = None
        if not self.enabled:
            self.reason = "LANGFUSE_TRACING_ENABLED explicitly disabled tracing"
            return
        if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
            self.enabled = False
            self.reason = "Langfuse is enabled by default, but LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required"
            return
        try:
            from langfuse import get_client
        except ImportError:
            self.enabled = False
            self.reason = "langfuse package is not installed; run python3 -m pip install -e ."
            return
        self.client = get_client()

    def export_many(self, results: list[HarnessResult]) -> dict[str, Any]:
        if not self.enabled or self.client is None:
            return {
                "backend": "langfuse",
                "default_eval_backend": True,
                "enabled": False,
                "exported": 0,
                "json_mirror_enabled": True,
                "reason": self.reason,
            }
        exported = 0
        errors = []
        for result in results:
            try:
                self.export(result)
                exported += 1
            except Exception as exc:  # pragma: no cover - depends on external service
                errors.append({"session_id": result.session.id, "error": str(exc)})
        try:
            self.client.flush()
        except Exception as exc:  # pragma: no cover - depends on external service
            errors.append({"session_id": "flush", "error": str(exc)})
        return {
            "backend": "langfuse",
            "default_eval_backend": True,
            "enabled": True,
            "exported": exported,
            "json_mirror_enabled": True,
            "errors": errors,
        }

    def export(self, result: HarnessResult) -> None:
        assert self.client is not None
        session = result.session
        with self._start_observation(
            name=f"email-calendar-lab/{session.scenario_id}",
            as_type="span",
            input={
                "scenario_id": session.scenario_id,
                "mode": session.mode,
                "provider": session.provider,
                "model": session.model,
            },
        ) as root:
            self._update_trace(root, session)
            self._export_generation(result)
            for tool_call in result.tool_trace:
                with self._start_observation(
                    name=f"tool:{tool_call['tool']}",
                    as_type="span",
                    input=tool_call["args"],
                ) as tool_span:
                    self._update(
                        tool_span,
                        output={
                            "result_count": tool_call["result_count"],
                            "evidence_ids": tool_call["evidence_ids"],
                        },
                    )
            with self._start_observation(
                name="eval",
                as_type="span",
                input={"scenario_id": result.run.scenario_id, "query": result.run.query},
            ) as eval_span:
                self._update(eval_span, output=result.evaluator_decision)
            self._update(
                root,
                output={
                    "answer": result.run.answer,
                    "passed": result.run.passed,
                    "failure_reason": result.run.failure_reason,
                    "root_cause": result.run.root_cause,
                },
            )

    def export_reflective_phase(self, reflections: list[ReflectionRecord], evolution_summary: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self.client is None:
            return {
                "backend": "langfuse",
                "default_eval_backend": True,
                "enabled": False,
                "exported": 0,
                "reason": self.reason,
            }
        try:
            with self._start_observation(
                name="email-calendar-lab/reflective-phase",
                as_type="span",
                input={"reflection_count": len(reflections)},
            ) as root:
                for reflection in reflections:
                    with self._start_observation(
                        name=f"reflection:{reflection.lesson_type}",
                        as_type="span",
                        input={"scenario_id": reflection.scenario_id, "session_id": reflection.session_id},
                    ) as span:
                        self._update(span, output=reflection.to_dict())
                self._update(root, output=evolution_summary)
            self.client.flush()
            return {"backend": "langfuse", "enabled": True, "exported": len(reflections), "errors": []}
        except Exception as exc:  # pragma: no cover - depends on external service
            return {"backend": "langfuse", "enabled": True, "exported": 0, "errors": [str(exc)]}

    def _export_generation(self, result: HarnessResult) -> None:
        with self._start_observation(
            name="deterministic-policy",
            as_type="generation",
            model=result.session.model,
            input=asdict(result.session.prompt_bundle),
        ) as generation:
            self._update(generation, output=result.run.answer)

    def _start_observation(self, name: str, as_type: str, **kwargs):
        if hasattr(self.client, "start_as_current_observation"):
            return self.client.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        if as_type == "generation" and hasattr(self.client, "start_as_current_generation"):
            return self.client.start_as_current_generation(name=name, **kwargs)
        return self.client.start_as_current_span(name=name, **kwargs)

    @staticmethod
    def _update(observation: Any, **kwargs) -> None:
        observation.update(**kwargs)

    @staticmethod
    def _update_trace(root: Any, session) -> None:
        if hasattr(root, "update_trace"):
            root.update_trace(
                name=f"email-calendar-lab/{session.scenario_id}",
                session_id=session.id,
                user_id="local-email-calendar-lab",
                tags=("email-calendar-lab", session.mode, session.provider),
                metadata={
                    "model": session.model,
                    "prompt_rules": session.prompt_bundle.rules,
                    "skill_ids": session.prompt_bundle.skill_ids,
                },
            )


def _load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env.langfuse.local"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

