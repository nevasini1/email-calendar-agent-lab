"""Tiny JSON status file so the dashboard can show the pipeline advancing live."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROGRESS_PATH = ROOT / "logs" / "run_progress.json"


def emit(phase: str, *, step: int, message: str = "", detail: dict | None = None) -> None:
    """Overwrite latest progress (single-writer run_cycle process)."""
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "step": step,
        "message": message,
        "detail": detail or {},
        "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    PROGRESS_PATH.write_text(json.dumps(payload, default=str))
