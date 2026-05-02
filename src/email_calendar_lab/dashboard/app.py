from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..run_history import build_run_history_entry
from ..agent import BASELINE_CONFIG
from ..fixtures import ALL_EVENTS, CONTACTS, EMAILS, HELDOUT_EVALS, NOW, PRODUCTION_SCENARIOS, STABLE_EVALS
from ..tool_broker import ToolBroker

PACKAGE_DIR = Path(__file__).resolve().parent
# Repo root (dashboard avoids importing run_cycle — pulls heavy DSPy/GEPA stack).
ROOT = PACKAGE_DIR.parents[2]


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


LOG_PATH = ROOT / "logs" / "run_latest.json"
RUN_HISTORY_PATH = ROOT / "logs" / "run_history.jsonl"
RUN_PROGRESS_PATH = ROOT / "logs" / "run_progress.json"
SESSIONS_DIR = ROOT / "logs" / "sessions"
EVALS_DIR = ROOT / "evals"
EXPECTED_FAILURE_CATEGORIES = (
    "time_zones",
    "recurring_meetings",
    "cancelled_events",
    "ambiguous_contacts",
    "attendees_vs_senders",
    "flight_emails",
    "free_busy_lookup",
    "last_before_anchor",
)

_pipeline_lock = threading.Lock()
_pipeline_state: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "output": "",
    "error": None,
}


def _safe_session_name(name: str) -> str | None:
    base = Path(name).name
    if not re.fullmatch(r"[a-zA-Z0-9._-]+\.json", base):
        return None
    return base


def _langfuse_ui_url() -> str:
    host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or "http://localhost:3000"
    return host.rstrip("/")


def _run_pipeline_blocking() -> None:
    global _pipeline_state
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / ".env.langfuse.local")
    lines: list[str] = []

    def reader(pipe: Any) -> None:
        try:
            for line in iter(pipe.readline, ""):
                lines.append(line)
        finally:
            pipe.close()

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "email_calendar_lab.run_cycle"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    t = threading.Thread(target=reader, args=(proc.stdout,))
    t.start()
    exit_code = proc.wait()
    t.join()
    text = "".join(lines)
    max_chars = 400_000
    if len(text) > max_chars:
        text = "…(truncated)\n" + text[-max_chars:]
    with _pipeline_lock:
        _pipeline_state["status"] = "success" if exit_code == 0 else "error"
        _pipeline_state["finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        _pipeline_state["exit_code"] = exit_code
        _pipeline_state["output"] = text


def _pipeline_thread_main() -> None:
    global _pipeline_state
    try:
        _run_pipeline_blocking()
    except Exception as exc:  # pragma: no cover
        with _pipeline_lock:
            _pipeline_state["status"] = "error"
            _pipeline_state["error"] = str(exc)
            _pipeline_state["finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / ".env.langfuse.local")
    yield


app = FastAPI(title="Email Calendar Agent Lab", lifespan=lifespan)
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _compute_run_history(limit: int) -> dict[str, Any]:
    limit = max(1, min(limit, 48))
    rows: list[dict[str, Any]] = []
    if RUN_HISTORY_PATH.is_file():
        lines = [ln for ln in RUN_HISTORY_PATH.read_text().splitlines() if ln.strip()]
        start_index = max(0, len(lines) - limit)
        for offset, line in enumerate(lines[-limit:], start=start_index + 1):
            try:
                rows.append(_enrich_history_row(json.loads(line), offset))
            except json.JSONDecodeError:
                continue
    if not rows and LOG_PATH.is_file():
        try:
            data = json.loads(LOG_PATH.read_text())
            rows = [_enrich_history_row(build_run_history_entry(data), 1, data)]
        except json.JSONDecodeError:
            pass
    return {
        "runs": rows,
        "count": len(rows),
        "path": str(RUN_HISTORY_PATH),
    }


def _enrich_history_row(row: dict[str, Any], history_index: int, latest_log: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = dict(row.get("runtime") or {})
    if not runtime and latest_log:
        runtime = dict(latest_log.get("runtime") or {})

    lf = row.get("langfuse_sessions") or {}
    provider = runtime.get("provider")
    model = runtime.get("model")
    if not provider:
        provider = "openai-live" if lf.get("enabled") is True and (lf.get("exported") or 0) > 0 else "deterministic-local"
    if not model:
        model = (row.get("current_config_loaded") or {}).get("model") or (row.get("current_config_final") or {}).get("model")
    sample_type = "live_openai" if provider == "openai-live" else "deterministic"

    enriched = dict(row)
    enriched["history_index"] = history_index
    enriched["runtime"] = {
        **runtime,
        "provider": provider,
        "model": model,
        "sample_type": sample_type,
    }
    return enriched


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _counter_dict(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(v for v in values if v).items()))


def _scenario_summary(scenarios: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "query": s.query,
            "category": s.category,
            "expected_tools": list(s.expected_tools),
            "expected_evidence_ids": list(s.expected_evidence_ids),
            "required_tool_args": s.required_tool_args,
        }
        for s in scenarios
    ]


def _eval_inventory() -> list[dict[str, Any]]:
    specs = [
        ("workflow", EVALS_DIR / "workflow.jsonl"),
        ("stable", EVALS_DIR / "stable.jsonl"),
        ("generated", EVALS_DIR / "generated.jsonl"),
        ("heldout", EVALS_DIR / "heldout.jsonl"),
    ]
    out: list[dict[str, Any]] = []
    for split, path in specs:
        rows = _read_jsonl(path)
        out.append(
            {
                "split": split,
                "path": str(path),
                "count": len(rows),
                "categories": _counter_dict([str(row.get("category") or row.get("workflow_type") or "workflow") for row in rows]),
                "lifecycle": _counter_dict([str(row.get("lifecycle") or split) for row in rows]),
                "promotion_status": _counter_dict([str(row.get("promotion_status") or row.get("lifecycle") or split) for row in rows]),
            }
        )
    return out


def _category_coverage(eval_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jsonl_rows = {
        "stable": _read_jsonl(EVALS_DIR / "stable.jsonl"),
        "generated": _read_jsonl(EVALS_DIR / "generated.jsonl"),
        "heldout": _read_jsonl(EVALS_DIR / "heldout.jsonl"),
    }

    def scenario_count(category: str, scenarios: tuple[Any, ...]) -> int:
        if category == "free_busy_lookup":
            return sum(1 for s in scenarios if "calendar.free_busy" in s.expected_tools)
        return sum(1 for s in scenarios if s.category == category)

    def row_count(category: str, split: str) -> int:
        rows = jsonl_rows.get(split, [])
        if category == "free_busy_lookup":
            return sum(1 for row in rows if "calendar.free_busy" in (row.get("expected_tools") or ()))
        return sum(1 for row in rows if row.get("category") == category)

    out: list[dict[str, Any]] = []
    for category in EXPECTED_FAILURE_CATEGORIES:
        out.append(
            {
                "category": category,
                "production": scenario_count(category, PRODUCTION_SCENARIOS),
                "stable": row_count(category, "stable"),
                "generated": row_count(category, "generated"),
                "heldout": row_count(category, "heldout"),
            }
        )
    return out


def _first_session_snapshot(latest: dict[str, Any] | None) -> dict[str, Any]:
    paths = (latest or {}).get("session_logs", {}).get("paths") or []
    for raw in paths:
        path = Path(raw)
        data = _read_json(path)
        if data:
            return {
                "path": str(path),
                "provider": data.get("provider"),
                "model": data.get("model"),
                "mode": data.get("mode"),
                "scenario_id": data.get("scenario_id"),
            }
    return {}


def _prompt_change_evidence(history: dict[str, Any], latest: dict[str, Any] | None) -> dict[str, Any]:
    runs = history.get("runs") or []
    promoted = next((row for row in runs if row.get("promotion_accepted") is True), None)
    row = promoted or (runs[-1] if runs else {})
    baseline_rules = row.get("baseline_rules") or (latest or {}).get("self_improvement", {}).get("current_prompt_rules") or []
    candidate_rules = row.get("candidate_rules") or (latest or {}).get("self_improvement", {}).get("candidate_prompt_rules") or []
    added = [rule for rule in candidate_rules if rule not in baseline_rules]
    removed = [rule for rule in baseline_rules if rule not in candidate_rules]
    return {
        "source_run_at": row.get("run_at"),
        "promotion_accepted": row.get("promotion_accepted"),
        "decision": row.get("promotion_decision") or (latest or {}).get("self_improvement", {}).get("decision"),
        "baseline_rules": baseline_rules,
        "candidate_rules": candidate_rules,
        "added_rules": added,
        "removed_rules": removed,
        "suite_baseline": row.get("suite_baseline"),
        "suite_candidate": row.get("suite_candidate"),
        "heldout_baseline": row.get("heldout_baseline"),
        "heldout_candidate": row.get("heldout_candidate"),
        "current_config": (latest or {}).get("current_config") or {},
    }


def _deliverable_status(latest: dict[str, Any] | None, history: dict[str, Any], eval_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eval_counts = {item["split"]: item["count"] for item in eval_sets}
    generated = eval_counts.get("generated", 0)
    heldout = eval_counts.get("heldout", 0)
    session_count = (latest or {}).get("session_logs", {}).get("count") or 0
    history_count = history.get("count") or 0
    imp = (latest or {}).get("self_improvement") or {}
    current_config = (latest or {}).get("current_config") or {}
    final_config = current_config.get("final") or current_config.get("loaded") or {}
    tools = ToolBroker().schemas
    return [
        {
            "label": "Base email/calendar agent",
            "ok": bool(final_config.get("model") or BASELINE_CONFIG.model),
            "detail": f"{final_config.get('model') or BASELINE_CONFIG.model} · {len(final_config.get('prompt_rules') or BASELINE_CONFIG.prompt_rules)} prompt rule tag(s)",
        },
        {
            "label": "Mock Gmail + Calendar tools",
            "ok": len(tools) >= 3,
            "detail": f"{len(tools)} tools · {len(EMAILS)} emails · {len(ALL_EVENTS)} calendar events · {len(CONTACTS)} contacts",
        },
        {
            "label": "Eval creation loop",
            "ok": generated > 0,
            "detail": f"{generated} generated eval(s) from observed failures",
        },
        {
            "label": "Self-improvement loop",
            "ok": bool(imp) and history_count > 0,
            "detail": f"{history_count} retained run row(s); latest decision: {imp.get('decision') or '—'}",
        },
        {
            "label": "Run logs and trace evidence",
            "ok": session_count > 0 and history_count > 0,
            "detail": f"{session_count} session trace file(s) in latest run; {history_count} history row(s)",
        },
        {
            "label": "Regression / held-out tracking",
            "ok": heldout > 0 and bool(imp.get("candidate_heldout_score")),
            "detail": f"{heldout} held-out eval(s); latest heldout {imp.get('current_heldout_score', {}).get('score', '—')} → {imp.get('candidate_heldout_score', {}).get('score', '—')}",
        },
        {
            "label": "Anti-overfitting guardrails",
            "ok": bool((latest or {}).get("candidate_eval_promotions")) and heldout > 0,
            "detail": "Generated evals remain candidate/quarantined; promotion requires suite gain and no heldout regression",
        },
    ]


def _fixture_summary() -> dict[str, Any]:
    return {
        "now": NOW.isoformat(),
        "emails": len(EMAILS),
        "calendar_events": len(ALL_EVENTS),
        "contacts": len(CONTACTS),
        "recurring_events": sum(1 for event in ALL_EVENTS if event.recurrence_id),
        "cancelled_events": sum(1 for event in ALL_EVENTS if event.status == "cancelled"),
        "flight_emails": sum(1 for email in EMAILS if "flight" in f"{email.subject} {email.body}".lower()),
        "ambiguous_first_names": {
            "Alex": [c.name for c in CONTACTS if c.name.startswith("Alex")],
            "Sarah/Sara": [c.name for c in CONTACTS if c.name.startswith(("Sarah", "Sara"))],
        },
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "langfuse_url": _langfuse_ui_url(),
            "project_root": str(ROOT),
        },
    )


@app.get("/api/history")
@app.get("/api/history/")
@app.get("/history")
@app.get("/history/")
@app.get("/api/run-history")
@app.get("/api/run-history/")
@app.get("/run-history")
@app.get("/run-history/")
async def api_run_history(limit: int = 12) -> dict[str, Any]:
    """Last N completed pipeline summaries from logs/run_history.jsonl, else one row from run_latest.json."""
    return _compute_run_history(limit)


@app.get("/api/latest-run")
async def api_latest_run() -> dict[str, Any]:
    if not LOG_PATH.is_file():
        return {"exists": False, "data": None}
    try:
        data = json.loads(LOG_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"exists": True, "path": str(LOG_PATH), "data": data}


@app.get("/api/sessions")
async def api_sessions(limit: int = 80) -> dict[str, Any]:
    if not SESSIONS_DIR.is_dir():
        return {"sessions": []}
    paths = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in paths[: max(1, min(limit, 200))]:
        st = path.stat()
        out.append(
            {
                "name": path.name,
                "mtime": st.st_mtime,
                "size": st.st_size,
            }
        )
    return {"sessions": out, "dir": str(SESSIONS_DIR)}


@app.get("/api/sessions/{filename}")
async def api_session_detail(filename: str) -> dict[str, Any]:
    safe = _safe_session_name(filename)
    if safe is None:
        raise HTTPException(status_code=400, detail="invalid session filename")
    path = SESSIONS_DIR / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"name": safe, "data": data}


@app.get("/api/run-progress")
async def api_run_progress() -> dict[str, Any]:
    """Live pipeline phase from logs/run_progress.json (written by run_cycle)."""
    if not RUN_PROGRESS_PATH.is_file():
        return {"exists": False, "progress": None}
    try:
        progress = json.loads(RUN_PROGRESS_PATH.read_text())
    except json.JSONDecodeError:
        return {"exists": False, "progress": None}
    return {"exists": True, "progress": progress}


@app.get("/api/pipeline/status")
async def api_pipeline_status() -> dict[str, Any]:
    with _pipeline_lock:
        return dict(_pipeline_state)


@app.post("/api/pipeline/run")
async def api_pipeline_run() -> dict[str, Any]:
    with _pipeline_lock:
        if _pipeline_state["status"] == "running":
            return {"started": False, "reason": "already_running"}
        _pipeline_state["status"] = "running"
        _pipeline_state["started_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        _pipeline_state["finished_at"] = None
        _pipeline_state["exit_code"] = None
        _pipeline_state["output"] = ""
        _pipeline_state["error"] = None
        try:
            RUN_PROGRESS_PATH.unlink(missing_ok=True)
        except OSError:
            pass

    thread = threading.Thread(target=_pipeline_thread_main, daemon=True)
    thread.start()
    return {"started": True}


@app.get("/api/meta")
async def api_meta(history_limit: int = 10) -> dict[str, Any]:
    hl = max(1, min(history_limit, 48))
    return {
        "project_root": str(ROOT),
        "log_path": str(LOG_PATH),
        "run_history_path": str(RUN_HISTORY_PATH),
        "sessions_dir": str(SESSIONS_DIR),
        "langfuse_url": _langfuse_ui_url(),
        "python": sys.executable,
        "run_history": _compute_run_history(hl),
    }


@app.get("/api/spec-readout")
async def api_spec_readout(history_limit: int = 10) -> dict[str, Any]:
    """Submission-facing audit view built from local artifacts, not canned demo data."""
    latest = _read_json(LOG_PATH)
    history = _compute_run_history(history_limit)
    eval_sets = _eval_inventory()
    session = _first_session_snapshot(latest)
    current_config = (latest or {}).get("current_config") or {}
    loaded_config = current_config.get("loaded") or current_config.get("final") or {}
    final_config = current_config.get("final") or loaded_config
    broker = ToolBroker()
    evolution = (latest or {}).get("evolution_decisions") or {}
    promotion_decisions = (latest or {}).get("candidate_eval_promotions") or []
    return {
        "project_root": str(ROOT),
        "runbook_path": str(ROOT / "UI_SELF_IMPROVEMENT_RUNBOOK.md"),
        "agent": {
            "baseline_config": {
                "name": BASELINE_CONFIG.name,
                "model": BASELINE_CONFIG.model,
                "prompt_rules": list(BASELINE_CONFIG.prompt_rules),
            },
            "loaded_config": loaded_config,
            "final_config": final_config,
            "session_provider": session.get("provider"),
            "session_model": session.get("model"),
            "session_mode": session.get("mode"),
            "first_session_path": session.get("path"),
        },
        "tools": [
            {
                "name": schema.name,
                "description": schema.description,
                "args": list(schema.args),
            }
            for schema in broker.schemas
        ],
        "fixtures": _fixture_summary(),
        "scenarios": {
            "production": _scenario_summary(PRODUCTION_SCENARIOS),
            "stable": _scenario_summary(STABLE_EVALS),
            "heldout": _scenario_summary(HELDOUT_EVALS),
        },
        "eval_sets": eval_sets,
        "coverage": _category_coverage(eval_sets),
        "generated_evals": _read_jsonl(EVALS_DIR / "generated.jsonl"),
        "prompt_change": _prompt_change_evidence(history, latest),
        "deliverables": _deliverable_status(latest, history, eval_sets),
        "anti_overfit": {
            "promotion_decisions": promotion_decisions,
            "decision_counts": {
                "accepted": evolution.get("accepted_count", 0),
                "rejected": evolution.get("rejected_count", 0),
                "quarantined": evolution.get("quarantined_count", 0),
            },
            "optimizer": evolution.get("optimizer"),
            "dspy_gepa": evolution.get("dspy_gepa"),
            "policy": [
                "stable evals remain fixed regressions",
                "generated evals are failure-derived candidates before promotion",
                "held-out evals are scored but not used to generate rules",
                "candidate prompt changes require suite gain and no heldout regression",
                "deliberately bad prompt variant must be rejected by the sanity gate",
            ],
        },
    }


@app.get("/api/langfuse-status")
async def api_langfuse_status() -> dict[str, Any]:
    """Fresh probe (SDK import + keys); unlike logs/run_latest.json this is not stale."""
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / ".env.langfuse.local")
    from ..langfuse_exporter import LangfuseSessionExporter

    ex = LangfuseSessionExporter()
    return {
        "enabled": ex.enabled,
        "reason": ex.reason,
        "has_client": ex.client is not None,
        "python": sys.executable,
    }


app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("EMAIL_CALENDAR_LAB_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("EMAIL_CALENDAR_LAB_UI_PORT", "8765"))
    uvicorn.run("email_calendar_lab.dashboard.app:app", host=host, port=port, reload=False)
