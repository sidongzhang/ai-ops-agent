"""
Skill Router：根据用户问题的意图，匹配最合适的 skill 并返回其调查步骤。
Skill 文件存放在 agent/skills/*.yaml，新增文件即自动生效，无需改代码。
"""
import os
import yaml
import logging

logger = logging.getLogger(__name__)

_SKILLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_skills() -> list[dict]:
    skills = []
    for fname in os.listdir(_SKILLS_DIR):
        if not fname.endswith('.yaml'):
            continue
        path = os.path.join(_SKILLS_DIR, fname)
        try:
            with open(path, encoding='utf-8') as f:
                skill = yaml.safe_load(f)
            if skill and 'name' in skill and 'triggers' in skill and 'steps' in skill:
                skills.append(skill)
        except Exception as e:
            logger.warning(f'加载 skill 失败 {fname}: {e}')
    return skills


def get_skill_context(question: str) -> str:
    """
    根据问题返回最匹配的 skill 的调查步骤。
    匹配不到则返回空字符串。
    """
    skills = _load_skills()
    question_lower = question.lower()

    best_skill = None
    best_score = 0

    for skill in skills:
        score = sum(
            1 for trigger in skill.get('triggers', [])
            if trigger.lower() in question_lower
        )
        if score > best_score:
            best_score = score
            best_skill = skill

    if best_skill and best_score >= 1:
        logger.info(f"匹配到 skill: {best_skill['name']} (得分 {best_score})")
        return best_skill['steps']

    return ''


def list_skills() -> list[dict]:
    """返回所有已加载的 skill 摘要（name + description）"""
    return [
        {'name': s['name'], 'description': s['description']}
        for s in _load_skills()
    ]
