"""Skills: reusable markdown playbooks the agent reads, follows, and writes.

Each skill is a markdown file with a small front matter block:

    ---
    name: research-and-summarize
    description: Research a topic on the web and summarise it with sources
    triggers: research, look up, find out
    ---
    1. ...steps...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WORD_RE = re.compile(r"[a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:60] or "skill"


@dataclass
class Skill:
    name: str
    description: str
    body: str
    triggers: list[str] = field(default_factory=list)
    path: Path | None = None

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            f"triggers: {', '.join(self.triggers)}",
            "---",
            "",
            self.body.strip(),
            "",
        ]
        return "\n".join(lines)

    def as_dict(self, with_body: bool = False) -> dict:
        data = {"name": self.name, "description": self.description, "triggers": self.triggers}
        if with_body:
            data["body"] = self.body
        return data


def parse_skill(text: str, path: Path | None = None) -> Skill:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip().lower()] = value.strip()
            body = parts[2]
    name = meta.get("name") or (path.stem if path else "skill")
    triggers = [t.strip() for t in meta.get("triggers", "").split(",") if t.strip()]
    return Skill(
        name=slugify(name),
        description=meta.get("description", ""),
        body=body.strip(),
        triggers=triggers,
        path=path,
    )


class SkillLibrary:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def all(self) -> list[Skill]:
        if not self.directory.exists():
            return []
        return [
            parse_skill(p.read_text(encoding="utf-8"), p)
            for p in sorted(self.directory.glob("*.md"))
        ]

    def get(self, name: str) -> Skill | None:
        slug = slugify(name)
        return next((s for s in self.all() if s.name == slug), None)

    def match(self, query: str, limit: int = 2) -> list[Skill]:
        """Cheap keyword match: triggers, name and description overlap with the query."""
        q = query.lower()
        q_words = set(_WORD_RE.findall(q))
        scored: list[tuple[int, Skill]] = []
        for skill in self.all():
            score = sum(3 for t in skill.triggers if t.lower() in q)
            vocab = set(_WORD_RE.findall(f"{skill.name} {skill.description}".lower()))
            score += len({w for w in q_words & vocab if len(w) > 3})
            if score:
                scored.append((score, skill))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def index_text(self) -> str:
        skills = self.all()
        if not skills:
            return "(no skills yet)"
        return "\n".join(f"- {s.name}: {s.description}" for s in skills)

    def save(
        self,
        name: str,
        description: str,
        steps: list[str] | str,
        triggers: list[str] | None = None,
        overwrite: bool = False,
    ) -> Skill:
        slug = slugify(name)
        path = self.directory / f"{slug}.md"
        if path.exists() and not overwrite:
            raise FileExistsError(f"skill '{slug}' already exists")
        if isinstance(steps, str):
            body = steps.strip()
        else:
            body = "\n".join(f"{i}. {step.strip()}" for i, step in enumerate(steps, 1))
        skill = Skill(slug, description.strip(), body, triggers or [], path)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(skill.to_markdown(), encoding="utf-8")
        return skill
