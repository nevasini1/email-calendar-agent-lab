"""
LLM-authored eval scenarios grounded in fixture emails/events.

Profiles:
  * production — probes written to ``prod_model_*`` ids (see ``resolve_production_scenarios``).
  * stable — main regression suite rows written to ``stable_model_*`` ids.
  * heldout — promotion gate checks written to ``heldout_model_*`` ids.

When ``OPENAI_API_KEY`` is set and the profile's ``*_SCENARIOS_SOURCE`` is not
``static``, the configured model emits JSON scenarios validated against the mock
corpus. Otherwise the matching static tuple from ``fixtures`` is used.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, cast

from openai import OpenAI

from .fixtures import ALL_EVENTS, CONTACTS, EMAILS, HELDOUT_EVALS, NOW, PRODUCTION_SCENARIOS, STABLE_EVALS
from .models import Scenario

Profile = Literal["production", "stable", "heldout"]

_ALLOWED_TOOLS = frozenset({"gmail.search_emails", "calendar.search_events", "calendar.free_busy"})
_KNOWN_CATEGORIES = frozenset(
    {
        "cancelled_events",
        "attendees_vs_senders",
        "flight_emails",
        "ambiguous_contacts",
        "last_before_anchor",
        "time_zones",
        "recurring_meetings",
        "workflow_openai",
        "general",
    }
)
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{3,48}$", re.I)
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

_PROFILE: dict[
    Profile,
    dict[str, Any],
] = {
    "production": {
        "static": PRODUCTION_SCENARIOS,
        "count_env": "PRODUCTION_SCENARIO_COUNT",
        "default_count": 7,
        "max_count": 20,
        "source_env": "PRODUCTION_SCENARIOS_SOURCE",
        "id_prefix": "prod_model_",
        "split": "production",
    },
    "stable": {
        "static": STABLE_EVALS,
        "count_env": "STABLE_SCENARIO_COUNT",
        "default_count": 3,
        "max_count": 15,
        "source_env": "STABLE_SCENARIOS_SOURCE",
        "id_prefix": "stable_model_",
        "split": "stable",
    },
    "heldout": {
        "static": HELDOUT_EVALS,
        "count_env": "HELDOUT_SCENARIO_COUNT",
        "default_count": 3,
        "max_count": 15,
        "source_env": "HELDOUT_SCENARIOS_SOURCE",
        "id_prefix": "heldout_model_",
        "split": "heldout",
    },
}


def _evidence_universe() -> frozenset[str]:
    mail_ids = {e.id for e in EMAILS}
    event_ids = {ev.id for ev in ALL_EVENTS}
    return frozenset(mail_ids | event_ids)


def _client_timeout_seconds() -> float:
    raw = os.getenv("OPENAI_CLIENT_TIMEOUT_SEC", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 30.0
    return min(value, 180.0)


def _fixture_catalog_text() -> str:
    lines: list[str] = [
        f"Current time anchor NOW: {NOW.isoformat()}",
        "",
        "Contacts (resolve people by name):",
    ]
    for c in CONTACTS:
        lines.append(f"  - {c.name} <{c.email}> aliases={list(c.aliases)}")
    lines.extend(["", "Emails (use these ids in expected_evidence_ids):"])
    for e in EMAILS:
        subj = e.subject.replace("\n", " ")[:100]
        lines.append(f"  id={e.id} from={e.sender} subject={subj}")
    lines.extend(["", "Calendar events (use these ids in expected_evidence_ids):"])
    for ev in ALL_EVENTS:
        lines.append(
            f"  id={ev.id} title={ev.title!r} start={ev.start.isoformat()} "
            f"attendees={list(ev.attendees)} status={ev.status}"
        )
    lines.extend(
        [
            "",
            "Allowed tools (exact strings):",
            "  - gmail.search_emails",
            "  - calendar.search_events",
            "  - calendar.free_busy",
            "",
            "Prefer categories from:",
            ", ".join(sorted(_KNOWN_CATEGORIES - {"workflow_openai", "general"})),
            " (or \"general\" if none fit).",
        ]
    )
    return "\n".join(lines)


def _system_prompt_for_profile(profile: Profile, count: int, id_prefix: str) -> str:
    catalog_hint = (
        "Output ONLY valid JSON with top-level key \"scenarios\" (array). "
        f"Generate exactly {count} items. Each element must have: "
        f"id (unique snake_case, prefix {id_prefix}), query, "
        "expected_contains (array of 1-4 short strings that must appear in the final answer), "
        "category, expected_tools (non-empty subset of allowed tools), "
        "expected_evidence_ids (non-empty array of ids from the catalog), "
        "forbidden_contains (array, optional), required_tool_args (object, optional). "
        "Questions must be answerable using only those tools and the fixture data. "
        "Do not require sending email or mutating calendar — read/search only."
    )
    if profile == "production":
        return (
            "You write production-like discovery scenarios for a mocked Gmail + Calendar assistant. "
            + catalog_hint
            + " Cover diverse tools and realistic user wording."
        )
    if profile == "stable":
        return (
            "You write stable regression scenarios that will be merged into the main eval suite "
            "(stable ∪ failures-from-production). Prefer diverse categories and overlapping edge cases "
            "that catch prompt regressions. "
            + catalog_hint
        )
    return (
        "You write held-out validation scenarios used ONLY for promotion gates (not in the training suite). "
        "Make them challenging but still fully answerable from fixture data and read/search tools. "
        + catalog_hint
    )


def _parse_required_tool_args(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for tool, spec in raw.items():
        if tool not in _ALLOWED_TOOLS or not isinstance(spec, dict):
            continue
        inner: dict[str, Any] = {}
        for k, v in spec.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                inner[k] = v
        if inner:
            out[str(tool)] = inner
    return out


def _row_to_scenario(
    row: dict[str, Any],
    universe_ids: frozenset[str],
    *,
    id_prefix: str,
    split: str,
) -> Scenario | None:
    sid = row.get("id")
    query = row.get("query")
    if (
        not isinstance(sid, str)
        or not sid.startswith(id_prefix)
        or not _ID_RE.match(sid)
    ):
        return None
    if not isinstance(query, str) or not query.strip():
        return None

    exp_contains = row.get("expected_contains")
    if not isinstance(exp_contains, list) or not all(isinstance(x, str) and x.strip() for x in exp_contains):
        return None

    cat = row.get("category", "general")
    if not isinstance(cat, str) or not cat.strip():
        cat = "general"
    if cat not in _KNOWN_CATEGORIES:
        normalized = cat.strip().lower().replace("-", "_").replace(" ", "_")
        cat = normalized if _CATEGORY_RE.match(normalized) else "general"

    tools_raw = row.get("expected_tools")
    if not isinstance(tools_raw, list):
        return None
    tools: list[str] = []
    for t in tools_raw:
        if isinstance(t, str) and t in _ALLOWED_TOOLS:
            tools.append(t)
    if not tools:
        return None

    ev_raw = row.get("expected_evidence_ids")
    if not isinstance(ev_raw, list):
        return None
    evidence: list[str] = []
    for eid in ev_raw:
        if isinstance(eid, str) and eid in universe_ids:
            evidence.append(eid)
    if not evidence:
        return None

    forb_raw = row.get("forbidden_contains")
    forbidden: tuple[str, ...] = ()
    if isinstance(forb_raw, list):
        forbidden = tuple(x.strip() for x in forb_raw if isinstance(x, str) and x.strip())

    req_args = _parse_required_tool_args(row.get("required_tool_args"))

    split_lit = cast(Literal["production", "stable", "heldout"], split)
    return Scenario(
        id=sid.strip(),
        query=query.strip(),
        expected_contains=tuple(x.strip() for x in exp_contains),
        category=cat.strip(),
        expected_tools=tuple(tools),
        split=split_lit,
        expected_evidence_ids=tuple(evidence),
        forbidden_contains=forbidden,
        required_tool_args=req_args,
    )


def _generate_json_payload(
    *,
    client: OpenAI,
    model: str,
    profile: Profile,
    count: int,
    id_prefix: str,
    feedback: str | None,
) -> dict[str, Any] | None:
    catalog = _fixture_catalog_text()
    sys = _system_prompt_for_profile(profile, count, id_prefix)
    user_parts = [
        f"Profile={profile}. Produce scenarios grounded only in the catalog below.\n\n",
        catalog,
    ]
    if feedback:
        user_parts.insert(0, f"Fix these validation errors from your previous output:\n{feedback}\n\n")

    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": "".join(user_parts)},
    ]
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    try:
        kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs, max_completion_tokens=4096)
    except TypeError:
        kwargs.pop("response_format", None)
        resp = client.chat.completions.create(**kwargs, max_completion_tokens=4096)
    except Exception:
        return None

    text = (resp.choices[0].message.content or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def generate_scenarios_from_model(count: int, profile: Profile) -> tuple[Scenario, ...] | None:
    """Returns validated scenarios, or None if generation failed entirely."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    cfg = _PROFILE[profile]
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
    client = OpenAI(api_key=api_key, timeout=_client_timeout_seconds())
    universe_ids = _evidence_universe()
    id_prefix: str = cfg["id_prefix"]
    split: str = cfg["split"]
    feedback: str | None = None
    parsed: list[Scenario] = []

    for _attempt in range(2):
        payload = _generate_json_payload(
            client=client,
            model=model,
            profile=profile,
            count=count,
            id_prefix=id_prefix,
            feedback=feedback,
        )
        if not payload or not isinstance(payload.get("scenarios"), list):
            feedback = "Missing \"scenarios\" array or invalid JSON."
            continue

        rows = payload["scenarios"]
        parsed = []
        errors: list[str] = []

        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"item {i}: not an object")
                continue
            sc = _row_to_scenario(row, universe_ids, id_prefix=id_prefix, split=split)
            if sc is None:
                errors.append(f"item {i}: failed validation (ids/tools/evidence/query)")
                continue
            if any(p.id == sc.id for p in parsed):
                errors.append(f"duplicate id {sc.id}")
                continue
            parsed.append(sc)

        if len(parsed) >= count:
            return tuple(parsed[:count])

        feedback = "; ".join(errors[:12]) if errors else f"Need at least {count} valid scenarios; got {len(parsed)}."

    return tuple(parsed) if parsed else None


def _resolve_profile(profile: Profile) -> tuple[tuple[Scenario, ...], str]:
    cfg = _PROFILE[profile]
    static: tuple[Scenario, ...] = cfg["static"]
    max_c: int = cfg["max_count"]
    default_c: int = cfg["default_count"]
    count = max(1, min(max_c, int(os.environ.get(cfg["count_env"], str(default_c)))))
    force_static = os.getenv(cfg["source_env"], "").strip().lower() == "static"

    if force_static or not os.getenv("OPENAI_API_KEY", "").strip():
        scenarios = static[:count] if count <= len(static) else static
        if len(scenarios) < count:
            scenarios = static
        return scenarios, "static"

    generated = generate_scenarios_from_model(count, profile)
    if generated and len(generated) >= count:
        return tuple(generated[:count]), "model"

    combined: list[Scenario] = []
    seen: set[str] = set()
    if generated:
        for s in generated:
            if s.id not in seen:
                combined.append(s)
                seen.add(s.id)
    for s in static:
        if len(combined) >= count:
            break
        if s.id not in seen:
            combined.append(s)
            seen.add(s.id)
    src = "hybrid" if generated else "static"
    return tuple(combined[:count]), src


def resolve_production_scenarios() -> tuple[tuple[Scenario, ...], str]:
    return _resolve_profile("production")


def resolve_stable_scenarios() -> tuple[tuple[Scenario, ...], str]:
    return _resolve_profile("stable")


def resolve_heldout_scenarios() -> tuple[tuple[Scenario, ...], str]:
    return _resolve_profile("heldout")


def generate_production_scenarios_from_model(count: int) -> tuple[Scenario, ...] | None:
    """Backward-compatible alias."""
    return generate_scenarios_from_model(count, "production")
