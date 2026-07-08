# nova_skills.py
# Nova Skills Library (ClickUp 86barguac) — structured per-category
# instruction files that nova_orchestrator.py prepends to a coding task's
# context before invoking the model. A 200-token skill prompt that
# precisely frames a task beats 800 tokens of re-explanation that still
# leaves ambiguity, and the saving compounds across every orchestrated
# task (see Nova Reference — Token Efficiency Strategy v1.0).
#
# Category comes from an explicit caller-supplied parameter, not a
# ClickUp task tag as the original spec assumed — nothing in Nova's own
# runtime code reads ClickUp today, only this interactive session does.
# See CLAUDE.md's "Nova Coding Sub-Agent" section.

import re
from pathlib import Path

SKILLS_DIR = Path("C:/Nova/skills")

VERSION_PATTERN = re.compile(r"Version:\s*([0-9.]+)")


def load_skill(category: str) -> str:
    """
    Read skills/{category}.md, or "" if no such skill file exists —
    graceful fallback, per the spec's own pseudocode. No category is not
    an error; it just means no context gets injected.
    """
    skill_path = SKILLS_DIR / f"{category}.md"
    if not skill_path.exists():
        return ""
    return skill_path.read_text(encoding="utf-8")


def get_skill_version(category: str) -> str | None:
    """
    Extract the 'Version: X.X' line from a skill file, for pinning in
    agent_log.jsonl per the skill files' own maintenance note ("pin skill
    version in logs for traceability"). None if the skill or its version
    header doesn't exist.
    """
    content = load_skill(category)
    if not content:
        return None
    match = VERSION_PATTERN.search(content)
    return match.group(1) if match else None
