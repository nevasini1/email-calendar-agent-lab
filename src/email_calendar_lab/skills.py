from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from openai import OpenAI

if TYPE_CHECKING:
    from .reflection import ReflectionRecord

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills"


@dataclass(frozen=True)
class SkillDoc:
    id: str
    path: str
    title: str
    trigger: str
    summary: str
    categories: tuple[str, ...]

    def to_prompt_summary(self) -> str:
        return f"{self.id}: {self.summary}"


@dataclass(frozen=True)
class CandidateSkill:
    id: str
    source_reflection_id: str
    title: str
    lesson_type: str
    trigger: str
    procedure: tuple[str, ...]
    validation_evals: tuple[str, ...]
    status: str = "quarantined"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillLibrary:
    def __init__(self, skill_dir: Path = SKILL_DIR) -> None:
        self.skill_dir = skill_dir

    def match(self, category: str, query: str) -> tuple[SkillDoc, ...]:
        ids = self._model_skill_ids(category, query) or self._keyword_skill_ids(category, query)
        return tuple(skill for skill in (self.load(skill_id) for skill_id in ids) if skill is not None)

    def _model_skill_ids(self, category: str, query: str) -> list[str] | None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        available = sorted(path.stem for path in self.skill_dir.glob("*.md"))
        if not available:
            return None
        client = OpenAI(api_key=api_key, timeout=_client_timeout_seconds())
        model = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
        messages = [
            {
                "role": "system",
                "content": (
                    "Select skill ids for an email/calendar scenario. "
                    "Return JSON: {\"skill_ids\": [ ... ]}. Choose only from provided ids."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"category": category, "query": query, "available_skill_ids": available}),
            },
        ]
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        try:
            kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs, max_completion_tokens=600)
        except TypeError:
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs, max_tokens=600)
        except Exception:
            return None
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        ids = payload.get("skill_ids") if isinstance(payload, dict) else None
        if not isinstance(ids, list):
            return None
        selected: list[str] = []
        for item in ids:
            if isinstance(item, str) and item in available and item not in selected:
                selected.append(item)
        return selected or None

    def _keyword_skill_ids(self, category: str, query: str) -> list[str]:
        available = sorted(path.stem for path in self.skill_dir.glob("*.md"))
        if not available:
            return []
        context = f"{category} {query}".lower()
        tokens = {tok for tok in re.findall(r"[a-z0-9_]+", context) if len(tok) >= 3}
        scored: list[tuple[int, str]] = []
        for skill_id in available:
            doc = self.load(skill_id)
            if doc is None:
                continue
            blob = " ".join([skill_id, doc.title, doc.trigger, doc.summary]).lower()
            score = sum(1 for tok in tokens if tok in blob)
            if score > 0:
                scored.append((score, skill_id))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [skill_id for _score, skill_id in scored[:3]]

    def load(self, skill_id: str) -> SkillDoc | None:
        path = self.skill_dir / f"{skill_id}.md"
        if not path.exists():
            return None
        text = path.read_text()
        title = _extract_heading(text)
        trigger = _extract_section(text, "Trigger")
        summary = " ".join(_extract_section(text, "Procedure").splitlines()[:2]).strip()
        return SkillDoc(skill_id, str(path), title, trigger, summary or trigger, ())


class SkillMiner:
    """Distills useful successes into quarantined candidate skill proposals."""

    def mine(self, reflections: list["ReflectionRecord"]) -> list[CandidateSkill]:
        candidates = []
        seen: set[str] = set()
        for reflection in reflections:
            if not reflection.passed or reflection.recommended_artifact != "candidate_skill":
                continue
            skill_id = self._skill_id(reflection)
            if skill_id in seen:
                continue
            seen.add(skill_id)
            candidates.append(
                CandidateSkill(
                    id=skill_id,
                    source_reflection_id=reflection.id,
                    title=f"Recovered Pattern: {reflection.lesson_type.replace('_', ' ').title()}",
                    lesson_type=reflection.lesson_type,
                    trigger=f"Similar scenario to {reflection.scenario_id}",
                    procedure=(
                        "Reuse the tool sequence from the successful session.",
                        "Keep the answer constrained to returned evidence IDs.",
                        "Promote only after category evals pass without heldout regression.",
                    ),
                    validation_evals=(reflection.scenario_id,),
                )
            )
        return candidates

    @staticmethod
    def _skill_id(reflection: "ReflectionRecord") -> str:
        return f"candidate_skill_{reflection.lesson_type}_{reflection.scenario_id}"


def _extract_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "Untitled Skill"


def _extract_section(text: str, section: str) -> str:
    marker = f"## {section}"
    if marker not in text:
        return ""
    after = text.split(marker, 1)[1]
    next_section = after.split("\n## ", 1)[0]
    return next_section.strip()


def _client_timeout_seconds() -> float:
    raw = os.getenv("OPENAI_CLIENT_TIMEOUT_SEC", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 30.0
    return min(value, 180.0)

