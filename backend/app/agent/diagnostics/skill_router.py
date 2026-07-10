"""Match a user question to a built-in diagnostic playbook."""
import logging
import os
from functools import lru_cache

import yaml

log = logging.getLogger(__name__)

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


@lru_cache(maxsize=1)
def _load_skills() -> tuple[dict, ...]:
    skills = []
    for fname in sorted(os.listdir(_SKILLS_DIR)):
        if not fname.endswith(".yaml"):
            continue
        path = os.path.join(_SKILLS_DIR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                skill = yaml.safe_load(f)
            if skill and {"name", "triggers", "steps"} <= skill.keys():
                skills.append(skill)
        except Exception as exc:
            log.warning(f"[skill-router] 加载 {fname} 失败: {exc}")
    log.info(f"[skill-router] 已加载 {len(skills)} 个 skill: {[s['name'] for s in skills]}")
    return tuple(skills)


def match_skill(question: str, disabled_names: set[str] | None = None) -> dict | None:
    """Return the best matching enabled playbook with its match score."""
    if not question:
        return None
    disabled_names = disabled_names or set()
    q = question.lower()
    best, best_score = None, 0
    for skill in _load_skills():
        if skill["name"] in disabled_names:
            continue
        score = sum(1 for t in skill["triggers"] if t.lower() in q)
        if score > best_score:
            best_score, best = score, skill
    if best and best_score >= 1:
        log.info(f"[skill-router] 匹配 skill={best['name']} score={best_score}")
        return {**best, "score": best_score}
    return None


def get_skill_steps(question: str, disabled_names: set[str] | None = None) -> str:
    """Return the steps of the best-matching playbook, or an empty string."""
    skill = match_skill(question, disabled_names)
    return skill["steps"] if skill else ""


def list_skills() -> list[dict]:
    """Return all built-in playbooks for management and introspection."""
    return [
        {
            "name": skill["name"],
            "description": skill["description"],
            "triggers": list(skill["triggers"]),
            "steps": skill["steps"],
        }
        for skill in _load_skills()
    ]
