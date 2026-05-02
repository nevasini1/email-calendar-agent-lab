from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

from .models import AgentRun, EvalCase

_RULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_DEFAULT_MODEL = "gpt-5.4-nano"
_BLOCKED_PROMOTION_RULES = {"answer_fast_without_new_evidence"}


def _append_rule(rules: list[str], rule: str) -> bool:
    if rule in _BLOCKED_PROMOTION_RULES or rule in rules:
        return False
    rules.append(rule)
    return True


def _client_timeout_seconds() -> float:
    raw = os.getenv("OPENAI_CLIENT_TIMEOUT_SEC", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 30.0
    return min(value, 180.0)


def _openai_client() -> OpenAI | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAI(api_key=api_key, timeout=_client_timeout_seconds())


def _chat_json(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    client = _openai_client()
    if client is None:
        return None
    model = os.getenv("OPENAI_MODEL", _DEFAULT_MODEL)
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    try:
        kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs, max_completion_tokens=1200)
    except TypeError:
        kwargs.pop("response_format", None)
        resp = client.chat.completions.create(**kwargs, max_tokens=1200)
    except Exception:
        return None
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def infer_root_cause(run: AgentRun, default_category: str | None = None) -> str | None:
    if run.passed:
        return None
    if run.root_cause:
        return run.root_cause

    fallback = (default_category or "unknown_failure").strip() or "unknown_failure"
    messages = [
        {
            "role": "system",
            "content": (
                "Classify the likely root cause for an eval failure. "
                "Return JSON: {\"root_cause\":\"snake_case_category\"}. "
                "Use short reusable categories, not scenario IDs."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "scenario_id": run.scenario_id,
                    "query": run.query,
                    "failure_reason": run.failure_reason,
                    "tool_calls": [
                        {"tool": c.tool, "args": c.args, "result_count": c.result_count, "evidence_ids": list(c.evidence_ids)}
                        for c in run.tool_calls
                    ],
                    "fallback_category": fallback,
                },
                default=str,
            ),
        },
    ]
    payload = _chat_json(messages)
    raw = (payload or {}).get("root_cause")
    if isinstance(raw, str):
        candidate = raw.strip().lower().replace(" ", "_").replace("-", "_")
        if _CATEGORY_RE.match(candidate):
            return candidate
    return fallback


def infer_lesson_type(run: AgentRun, default_category: str | None = None) -> str:
    if run.passed:
        return "useful_success"

    allowed = {
        "bad_temporal_reasoning",
        "bad_tool_args",
        "missing_evidence",
        "ambiguous_contact",
        "timezone_loss",
        "unknown_failure",
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Map a failure to one lesson type. "
                "Return JSON: {\"lesson_type\":\"one_of_allowed\"}. "
                "Allowed: bad_temporal_reasoning, bad_tool_args, missing_evidence, "
                "ambiguous_contact, timezone_loss, unknown_failure."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "scenario_id": run.scenario_id,
                    "root_cause": run.root_cause or default_category,
                    "failure_reason": run.failure_reason,
                }
            ),
        },
    ]
    payload = _chat_json(messages)
    lt = (payload or {}).get("lesson_type")
    if isinstance(lt, str) and lt in allowed:
        return lt

    reason = (run.failure_reason or "").lower()
    category = (run.root_cause or default_category or "").lower()
    if category == "time_zones" or "timezone" in reason or "2:35 pm et" in reason:
        return "timezone_loss"
    if category in {"last_before_anchor", "recurring_meetings"}:
        return "bad_temporal_reasoning"
    if category == "ambiguous_contacts":
        return "ambiguous_contact"
    if "tool args" in reason or "include_cancelled" in reason:
        return "bad_tool_args"
    if "evidence" in reason or "forbidden answer" in reason:
        return "missing_evidence"
    return "unknown_failure"


def propose_prompt_rules(current_rules: tuple[str, ...], failures: list[AgentRun]) -> tuple[str, ...]:
    rules = list(current_rules)
    if not failures:
        return tuple(rules)

    for failure in failures:
        for rule in _actionable_rules_for_failure(failure):
            _append_rule(rules, rule)

    summarized = [
        {
            "scenario_id": f.scenario_id,
            "root_cause": f.root_cause,
            "failure_reason": f.failure_reason,
            "tools": [c.tool for c in f.tool_calls],
            "evidence_ids": [eid for c in f.tool_calls for eid in c.evidence_ids][:8],
        }
        for f in failures
    ]

    payload = _chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Propose concise prompt rules that could reduce failures for a mocked "
                    "email/calendar QA agent. Return JSON: {\"rules\":[\"snake_case\", ...]}. "
                    "Prefer 1-6 short reusable rule names."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"current_rules": list(current_rules), "failures": summarized}, default=str),
            },
        ]
    )
    proposed = payload.get("rules") if payload else None
    if isinstance(proposed, list):
        for item in proposed:
            if not isinstance(item, str):
                continue
            rule = item.strip().lower().replace(" ", "_").replace("-", "_")
            if _RULE_NAME_RE.match(rule):
                _append_rule(rules, rule)

    if len(rules) > len(current_rules):
        return tuple(rules)

    for failure in failures:
        rc = (failure.root_cause or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not _CATEGORY_RE.match(rc):
            continue
        fallback = f"improve_{rc}"
        _append_rule(rules, fallback)
    return tuple(rules)


def _actionable_rules_for_failure(failure: AgentRun) -> tuple[str, ...]:
    text = " ".join(
        value
        for value in (
            failure.scenario_id,
            failure.query,
            failure.root_cause,
            failure.failure_reason,
        )
        if value
    ).lower()
    rules: list[str] = []
    if "cancelled" in text or "include_cancelled" in text:
        rules.append("exclude_cancelled_events")
    if "attendees_vs_senders" in text or "sender" in text or "human" in text:
        rules.append("prefer_human_participants")
    if "flight" in text and ("where" in text or "city" in text or "destination" in text):
        rules.append("parse_flight_destination")
    if "time_zone" in text or "timezone" in text or "2:35 pm et" in text or "arrival" in text or "arrive" in text:
        rules.append("preserve_source_timezones")
    if "ambiguous_contacts" in text or "which alex" in text or "alex chen" in text:
        rules.append("clarify_ambiguous_contacts")
    if "last_before_anchor" in text or ("before" in text and "offsite" in text):
        rules.append("respect_temporal_anchors")
    return tuple(dict.fromkeys(rules))


def judge_acceptance(
    *,
    current_score: dict,
    candidate_score: dict,
    current_heldout: dict,
    candidate_heldout: dict,
) -> tuple[bool | None, str | None]:
    payload = _chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Decide whether to promote a candidate prompt config. "
                    "Return JSON: {\"accept\": true|false, \"reason\": \"short rationale\"}. "
                    "Focus on suite gain quality and robustness, not just tiny noise."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_eval": current_score,
                        "candidate_eval": candidate_score,
                        "current_heldout": current_heldout,
                        "candidate_heldout": candidate_heldout,
                    },
                    default=str,
                ),
            },
        ]
    )
    if not payload:
        return None, None
    accept = payload.get("accept")
    reason = payload.get("reason")
    if isinstance(accept, bool):
        return accept, str(reason).strip() if isinstance(reason, str) else None
    return None, None


def judge_eval_promotion(
    *,
    eval_case: EvalCase,
    improved: bool,
    heldout_safe: bool,
    candidate_score: dict,
    candidate_heldout: dict,
) -> tuple[bool | None, str | None]:
    payload = _chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Decide if one candidate eval should be promoted now. "
                    "Return JSON: {\"promote\": true|false, \"reason\": \"short rationale\"}. "
                    "Be conservative unless signal is generalizable."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "eval_case": {
                            "id": eval_case.id,
                            "query": eval_case.query,
                            "category": eval_case.category,
                            "source_failure": eval_case.source_failure,
                            "root_cause": eval_case.root_cause,
                            "expected_tools": list(eval_case.expected_tools),
                            "expected_evidence_ids": list(eval_case.expected_evidence_ids),
                        },
                        "context": {
                            "improved": improved,
                            "heldout_safe": heldout_safe,
                            "candidate_eval_score": candidate_score.get("score"),
                            "candidate_heldout_score": candidate_heldout.get("score"),
                        },
                    },
                    default=str,
                ),
            },
        ]
    )
    if not payload:
        return None, None
    promote = payload.get("promote")
    reason = payload.get("reason")
    if isinstance(promote, bool):
        return promote, str(reason).strip() if isinstance(reason, str) else None
    return None, None
