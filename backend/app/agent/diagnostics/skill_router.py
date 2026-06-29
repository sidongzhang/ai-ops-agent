"""Skill router: match a user question to a pre-defined diagnostic playbook."""
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


def get_skill_steps(question: str) -> str:
    """Return the steps of the best-matching skill, or '' if none match."""
    if not question:
        return ""
    q = question.lower()
    best, best_score = None, 0
    for skill in _load_skills():
        score = sum(1 for t in skill["triggers"] if t.lower() in q)
        if score > best_score:
            best_score, best = score, skill
    if best and best_score >= 1:
        log.info(f"[skill-router] 匹配 skill={best['name']} score={best_score}")
        return best["steps"]
    return ""


def list_skills() -> list[dict]:
    """Return name + description for all loaded skills (for introspection)."""
    return [{"name": s["name"], "description": s["description"]} for s in _load_skills()]
