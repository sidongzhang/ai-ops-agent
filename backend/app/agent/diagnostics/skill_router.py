"""Playbook 语义路由（Embedding）+ 阈值拒识。

升级动机：旧版关键词计数 score>=1 即注入——任何单个触发词命中就注入剧本，
无拒识；同义表述（"内存不够"/"OOM被杀"/"内存打满"）覆盖不住，会把不相关
剧本注入上下文（112 条基线已证明不匹配的剧本注入是负收益）。

混合策略（keyword 优先、语义兜底）：
  1. 关键词计数 score>=2（两个以上触发词命中）→ 直接采纳（字面信号强）
  2. 否则走 Embedding 语义路由：question 与剧本描述+触发词拼串的余弦相似度，
     达到阈值（默认 0.62）才注入；低于阈值 → 拒识（返回 None，让 Agent 自由诊断）
  3. Embedding 不可用时回退旧关键词逻辑（score>=1）
"""
import logging
import os
import yaml
from functools import lru_cache

from .knowledge.store import _embed  # 复用同一 embedding 端点（DeepSeek/Ollama 通用）

log = logging.getLogger(__name__)

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
SEMANTIC_THRESHOLD = 0.62          # 语义相似度注入阈值（低于则拒识）
KEYWORD_STRONG_SCORE = 2           # 关键词直接采纳分数


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


@lru_cache(maxsize=1)
def _skill_embeddings() -> tuple[dict, ...] | None:
    """每个剧本的描述 + 触发词拼串预 embed（进程级缓存）。"""
    import yaml

    skills = _load_skills()
    texts = []
    for skill in skills:
        trigger_text = ", ".join(skill["triggers"])
        texts.append(f"{skill['name']}。{skill['description']}。关键词: {trigger_text}")
    from .knowledge.store import _embed

    vecs = _embed(texts)
    if not vecs or len(vecs) != len(skills):
        log.warning("[skill-router] 剧本 embedding 不可用，语义路由降级为关键词匹配")
        return None
    return tuple(
        {"name": skill["name"], "embedding": vec}
        for skill, vec in zip(skills, vecs)
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def match_skill(question: str, disabled_names: set[str] | None = None) -> dict | None:
    """返回最佳匹配剧本（混合：关键词优先，语义兜底+拒识）。"""
    if not question:
        return None
    disabled_names = disabled_names or set()
    q = question.lower()

    # 1) 关键词命中即采纳（字面信号是强证据；实验证明语义对短剧本描述区分力不足，
    #    语义路由仅在关键词零命中时作为增量路径，见 docs/evals 路由基准实验）
    best, best_score = None, 0
    for skill in _load_skills():
        if skill["name"] in disabled_names:
            continue
        score = sum(1 for t in skill["triggers"] if t.lower() in q)
        if score > best_score:
            best_score, best = score, skill
    if best and best_score >= 1:
        log.info(f"[skill-router] 关键词匹配 skill={best['name']} score={best_score}")
        return {**best, "score": best_score}

    # 2) 语义路由 + 阈值拒识
    embeddings = _skill_embeddings()
    if embeddings:
        from .knowledge.store import _embed

        qv = _embed([question])
        if qv:
            sims = [(s["name"], _cosine(qv[0], s["embedding"])) for s in embeddings
                    if s["name"] not in disabled_names]
            sims.sort(key=lambda x: -x[1])
            name, sim = sims[0]
            if sim >= SEMANTIC_THRESHOLD:
                skill = next((s for s in _load_skills() if s["name"] == name), None)
                if skill:
                    log.info(f"[skill-router] 语义匹配 skill={name} sim={sim:.3f}")
                    return {**skill, "score": round(sim, 3), "via": "semantic"}
            else:
                # 拒识：问题与所有剧本都不匹配 → 自由诊断（旧版会硬塞一个剧本）
                log.info(f"[skill-router] 拒识（best sim={sim:.3f} < {SEMANTIC_THRESHOLD}），不注入剧本")
                return None

    # 3) embedding 不可用：回退旧逻辑（score>=1）
    if best and best_score >= 1:
        log.info(f"[skill-router] 关键词回退匹配 skill={best['name']} score={best_score}")
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
