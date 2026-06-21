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


# project knowledge subdir -> one-line usage directive (relative to ./project)
_PK_USAGE = {
    "src-repo": "source code: read / grep `./project/src-repo`",
    "codegraph-out": "code graph: query with the `codegraph` CLI (query / explore / node / callers / impact) over `./project/codegraph-out`",
    "graphify-out": "domain knowledge graph: run `graphify query \"<question>\"` over `./project/graphify-out`",
    "scan-out": "static scan findings: read `./project/scan-out`",
    "docs-out": "product docs: read `./project/docs-out`",
}
# canonical render order
_PK_ORDER = ["src-repo", "docs-out", "graphify-out", "scan-out", "codegraph-out"]


def format_project_knowledge(project_root, present_subdirs) -> str:
    if not project_root or not present_subdirs:
        return ""
    present = set(present_subdirs)
    items = [_PK_USAGE[name] for name in _PK_ORDER if name in present and name in _PK_USAGE]
    if not items:
        return ""
    lines = [
        "## Project Knowledge (prior analysis, read-only at ./project)",
        "Reuse these prior results to gain context; do NOT redo the upfront analysis they already contain. "
        "If a query tool is missing, fall back to reading the files directly.",
        "",
    ]
    lines += [f"- {item}" for item in items]
    return "\n".join(lines)
