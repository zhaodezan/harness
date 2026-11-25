from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    skill_file: Path

    def load_instructions(self) -> str:
        text = self.skill_file.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)\Z", text, re.DOTALL)
        if not match:
            raise ValueError(f"Skill {self.name} frontmatter is invalid")
        return match.group(2).strip()

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


def discover_skills(directory: str | Path) -> list[Skill]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return []
    skills: list[Skill] = []
    for skill_file in sorted(root.glob("*/SKILL.md")):
        try:
            resolved = skill_file.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            text = resolved.read_text(encoding="utf-8")
        except (OSError, RuntimeError):
            continue
        match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n", text, re.DOTALL)
        if not match:
            continue
        fields = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key.strip()] = value.strip().strip("\"'")
        name = fields.get("name", "")
        description = fields.get("description", "")
        if not name or not description:
            continue
        skills.append(Skill(name=name, description=description, skill_file=resolved))
    return skills


def _skill_terms(text: str) -> set[str]:
    words = {item.lower() for item in re.findall(r"[A-Za-z0-9_]{3,}", text)}
    chars = re.findall(r"[㐀-鿿]", text)
    return words | {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}


def resolve_selected_skills(
    selected_names: list[str],
    managed_skills: list[Skill],
    legacy_skills: list[Skill] | None = None,
) -> tuple[list[Skill], list[str]]:
    by_name: dict[str, Skill] = {skill.name: skill for skill in (legacy_skills or [])}
    by_name.update({skill.name: skill for skill in managed_skills})
    selected: list[Skill] = []
    missing: list[str] = []
    for name in dict.fromkeys(selected_names):
        skill = by_name.get(name)
        if skill is None:
            missing.append(name)
        else:
            selected.append(skill)
    return selected, missing


def select_relevant_skills(query: str, skills: list[Skill], limit: int = 3) -> list[Skill]:
    query_terms = _skill_terms(query)
    ranked = []
    for skill in skills:
        overlap = len(query_terms & _skill_terms(f"{skill.name} {skill.description}"))
        if overlap:
            ranked.append((overlap, skill))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [skill for _, skill in ranked[:limit]]
