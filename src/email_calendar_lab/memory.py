from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .harness import HarnessResult
from .reflection import ReflectionRecord


class MemoryStore:
    """Persistent local memory for sessions, reflections, lessons, and promotions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                passed INTEGER NOT NULL,
                summary TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reflections (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                lesson_type TEXT NOT NULL,
                root_cause TEXT,
                generalizes INTEGER NOT NULL,
                recommended_artifact TEXT NOT NULL,
                confidence REAL NOT NULL,
                summary TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY,
                lesson_type TEXT NOT NULL,
                source_reflection_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifact_promotions (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        try:
            self.conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(id, kind, text);
                """
            )
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def remember_session(self, result: HarnessResult) -> None:
        run = result.run
        summary = f"{run.scenario_id}: passed={run.passed}; root_cause={run.root_cause}; answer={run.answer}"
        payload = {
            "session": asdict(result.session),
            "tool_trace": result.tool_trace,
            "evaluator_decision": result.evaluator_decision,
            "run": asdict(run),
        }
        self.conn.execute(
            """
            INSERT OR REPLACE INTO sessions
            (id, scenario_id, mode, provider, model, passed, summary, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.session.id,
                result.session.scenario_id,
                result.session.mode,
                result.session.provider,
                result.session.model,
                int(run.passed),
                summary,
                json.dumps(payload, default=str),
                result.session.started_at,
            ),
        )
        self._index(result.session.id, "session", summary)

    def remember_reflection(self, reflection: ReflectionRecord) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO reflections
            (id, session_id, scenario_id, lesson_type, root_cause, generalizes,
             recommended_artifact, confidence, summary, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reflection.id,
                reflection.session_id,
                reflection.scenario_id,
                reflection.lesson_type,
                reflection.root_cause,
                int(reflection.generalizes),
                reflection.recommended_artifact,
                reflection.confidence,
                reflection.summary,
                json.dumps(reflection.to_dict(), default=str),
                reflection.created_at,
            ),
        )
        self._index(reflection.id, "reflection", reflection.summary)

    def remember_lesson(self, reflection: ReflectionRecord, artifact_type: str, status: str) -> str:
        lesson_id = f"lesson_{reflection.id}"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO lessons
            (id, lesson_type, source_reflection_id, summary, artifact_type, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lesson_id,
                reflection.lesson_type,
                reflection.id,
                reflection.summary,
                artifact_type,
                status,
                reflection.created_at,
            ),
        )
        self._index(lesson_id, "lesson", reflection.summary)
        return lesson_id

    def remember_promotion(self, artifact_id: str, artifact_type: str, status: str, reason: str, created_at: str) -> None:
        promotion_id = f"promotion_{artifact_id}_{status}"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO artifact_promotions
            (id, artifact_id, artifact_type, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (promotion_id, artifact_id, artifact_type, status, reason, created_at),
        )

    def commit(self) -> None:
        self.conn.commit()

    def summary(self) -> dict[str, Any]:
        counts = {}
        for table in ("sessions", "reflections", "lessons", "artifact_promotions"):
            counts[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return {"path": str(self.path), "counts": counts, "fts_enabled": self._fts_enabled()}

    def _index(self, id_: str, kind: str, text: str) -> None:
        if not self._fts_enabled():
            return
        self.conn.execute("INSERT OR REPLACE INTO memory_fts (id, kind, text) VALUES (?, ?, ?)", (id_, kind, text))

    def _fts_enabled(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'"
        ).fetchone()
        return row is not None

