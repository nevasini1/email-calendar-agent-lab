from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .harness import HarnessResult, Session


class SessionStore:
    """Persists opencode-style session traces separately from summary logs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, result: HarnessResult) -> Path:
        path = self.root / f"{result.session.id}.json"
        path.write_text(json.dumps(self.session_payload(result), indent=2, default=str))
        return path

    def save_many(self, results: list[HarnessResult]) -> list[str]:
        return [str(self.save(result)) for result in results]

    def session_payload(self, result: HarnessResult) -> dict:
        payload = asdict(result.session)
        payload["tool_trace"] = result.tool_trace
        payload["evaluator_decision"] = result.evaluator_decision
        payload["final_run"] = asdict(result.run)
        return payload

    @staticmethod
    def to_dict(session: Session) -> dict:
        return asdict(session)

