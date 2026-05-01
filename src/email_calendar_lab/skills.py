from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    CATEGORY_SKILLS = {
        "cancelled_events": ("temporal_calendar_reasoning",),
        "last_before_anchor": ("temporal_calendar_reasoning",),
        "recurring_meetings": ("temporal_calendar_reasoning",),
        "flight_emails": ("flight_email_parsing",),
        "time_zones": ("flight_email_parsing",),
        "ambiguous_contacts": ("ambiguous_contact_resolution", "free_busy_lookup"),
        "attendees_vs_senders": ("ambiguous_contact_resolution",),
    }

    def __init__(self, skill_dir: Path = SKILL_DIR) -> None:
        self.skill_dir = skill_dir

    def match(self, category: str, query: str) -> tuple[SkillDoc, ...]:
        ids = list(self.CATEGORY_SKILLS.get(category, ()))
        lowered = query.lower()
        if "free time" in lowered and "free_busy_lookup" not in ids:
            ids.append("free_busy_lookup")
        if "flight" in lowered and "flight_email_parsing" not in ids:
            ids.append("flight_email_parsing")
        return tuple(skill for skill in (self.load(skill_id) for skill_id in ids) if skill is not None)

    def load(self, skill_id: str) -> SkillDoc | None:
        path = self.skill_dir / f"{skill_id}.md"
        if not path.exists():
            return None
        text = path.read_text()
        title = _extract_heading(text)
        trigger = _extract_section(text, "Trigger")
        summary = " ".join(_extract_section(text, "Procedure").splitlines()[:2]).strip()
        categories = tuple(category for category, ids in self.CATEGORY_SKILLS.items() if skill_id in ids)
        return SkillDoc(skill_id, str(path), title, trigger, summary or trigger, categories)


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

