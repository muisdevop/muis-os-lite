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
from datetime import datetime, timezone
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WORD_RE = re.compile(r"[a-z0-9]+")
_NUM_RE = re.compile(r"^\s*(\d+[.)]|[-*])\s*")


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
    source: str = "bundled"  # bundled | user | learned
    created: str = ""
    uses: int = 0

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            f"triggers: {', '.join(self.triggers)}",
        ]
        if self.source != "bundled":
            lines.append(f"source: {self.source}")
        if self.created:
            lines.append(f"created: {self.created}")
        lines += ["---", "", self.body.strip(), ""]
        return "\n".join(lines)

    def as_dict(self, with_body: bool = False) -> dict:
        data = {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "source": self.source,
            "created": self.created,
        }
        if with_body:
            data["body"] = self.body
            data["markdown"] = self.to_markdown()
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
        source=meta.get("source", "bundled") or "bundled",
        created=meta.get("created", ""),
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

    def match(self, query: str, limit: int = 2, min_score: int = 2) -> list[Skill]:
        """Cheap keyword match: trigger phrases, plus word overlap with name/description/triggers."""
        q = query.lower()
        q_stems = {w[:5] for w in _WORD_RE.findall(q) if len(w) > 3}
        scored: list[tuple[int, Skill]] = []
        for skill in self.all():
            score = sum(3 for t in skill.triggers if t.lower() in q)
            text = f"{skill.name} {skill.description} {' '.join(skill.triggers)}".lower()
            vocab = {w[:5] for w in _WORD_RE.findall(text) if len(w) > 3}
            score += len(q_stems & vocab)
            if score >= min_score:
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
        source: str = "user",
    ) -> Skill:
        slug = slugify(name)
        path = self.directory / f"{slug}.md"
        if path.exists() and not overwrite:
            raise FileExistsError(f"skill '{slug}' already exists")
        if isinstance(steps, str):
            body = steps.strip()
        else:
            clean = [_NUM_RE.sub("", step).strip() for step in steps]
            body = "\n".join(f"{i}. {step}" for i, step in enumerate([c for c in clean if c], 1))
        triggers = [t.strip() for t in (triggers or []) if t and t.strip()]
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        skill = Skill(slug, " ".join(description.split()), body, triggers, path, source, created)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(skill.to_markdown(), encoding="utf-8")
        return skill

    def write_markdown(self, name: str, markdown: str) -> Skill:
        """Replace a skill with edited markdown (front matter + steps). Name stays fixed."""
        existing = self.get(name)
        if existing is None or existing.path is None:
            raise KeyError(name)
        parsed = parse_skill(markdown, existing.path)
        parsed.name = existing.name
        if not parsed.body:
            raise ValueError("skill body is empty")
        parsed.created = parsed.created or existing.created
        if parsed.source == "bundled" and existing.source != "bundled":
            parsed.source = existing.source
        existing.path.write_text(parsed.to_markdown(), encoding="utf-8")
        return parsed

    def delete(self, name: str) -> bool:
        skill = self.get(name)
        if skill is None or skill.path is None:
            return False
        skill.path.unlink()
        return True
