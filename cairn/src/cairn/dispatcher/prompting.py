from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_prompt(group: str, name: str) -> str:
    return resources.files("cairn.dispatcher.prompts").joinpath(group).joinpath(name).read_text(encoding="utf-8")


def render_prompt(template: str, replacements: dict[str, str]) -> str:
    text = template
    for key, value in replacements.items():
        text = text.replace("{" + key + "}", value)
    return text


def format_fact_ids(fact_ids: list[str]) -> str:
    return format_json_block(fact_ids)


def format_open_intents(intents: list[dict[str, Any]]) -> str:
    return format_json_block(intents)


def format_hints(hints: list[dict[str, Any]]) -> str:
    return format_json_block(hints)


def format_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def format_skills(skills) -> str:
    if not skills:
        return ""
    lines = [
        "## Available Skills (prefer these)",
        "You have these skills installed at .claude/skills/<name>/SKILL.md. When a task matches "
        "a skill, READ its SKILL.md and follow it; prefer these skills over ad-hoc approaches.",
        "",
    ]
    for s in skills:
        desc = (s.description or "").strip()
        lines.append(f"- {s.name}: {desc}  (.claude/skills/{s.name}/SKILL.md)")
    return "\n".join(lines)
