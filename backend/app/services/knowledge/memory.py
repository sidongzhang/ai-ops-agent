"""结构化长期记忆服务：写入、验证回填、加权检索。

三个闭环：
  1. 写入：诊断成功 → 模型条目（source=model, confidence=0.5, validity=unconfirmed）
  2. 验证：工作流执行后回查通过 → validity=confirmed_success, confidence→0.9；失败 → failed
  3. 检索：pgvector 余弦 × confidence × validity 惩罚（failed 直接排除），防记忆污染
"""
import logging
import re

from sqlalchemy import text

from app.models.knowledge import (
    CONFIDENCE_MODEL,
    CONFIDENCE_VERIFIED,
    VALIDITY_CONFIRMED,
    VALIDITY_FAILED,
    VALIDITY_UNCONFIRMED,
    KnowledgeMemory,
)

log = logging.getLogger(__name__)

# validity → 检索权重系数
VALIDITY_WEIGHT = {
    "confirmed_success": 1.0,
    "unconfirmed": 0.5,
    "failed": 0.0,          # 处置无效的记忆直接排除（防污染核心）
}


def _embed_one(value: str) -> list | None:
    """复用 RAG store 的 embedding 端点；失败返回 None（该条目退化为不可向量检索）。"""
    from app.agent.diagnostics.knowledge.store import _embed

    vecs = _embed([value])
    return vecs[0] if vecs else None


def _memory_text(symptom: str, root_cause: str, remedy: str) -> str:
    return f"{symptom}\n根因: {root_cause}\n处置: {remedy}"[:2000]


def _extract_conclusion(answer: str) -> str:
    """从回答中提取「结论」段；兼容 markdown 粗体。"""
    m = re.search(r"\*{0,2}结论\*{0,2}[：:]\s*(.+)", answer or "")
    if m:
        return m.group(1).strip()[:800]
    return (answer or "").strip()[:400]


def _extract_remedy(answer: str) -> str:
    """从「建议」段提取首条处置；无则留空。"""
    m = re.search(r"\*{0,2}建议\*{0,2}[：:]\s*(.+?)(?:\n\*{2}|\Z)", answer or "", re.DOTALL)
    if not m:
        return ""
    lines = [ln.strip().lstrip("-• ").strip() for ln in m.group(1).splitlines() if ln.strip()]
    return (lines[0] if lines else "")[:800]


def save_memory(
    *,
    org_id: int,
    system_id: int,
    question: str,
    answer: str,
    report_id: int | None = None,
    workflow_id: int | None = None,
    source: str = "model",
    confidence: float | None = None,
    validity: str = VALIDITY_UNCONFIRMED,
) -> int | None:
    """写入一条结构化记忆。失败静默（记忆绝不能阻断诊断/工作流）。"""
    from app.core.database import engine
    from sqlmodel import Session

    try:
        symptom = (question or "").strip()[:500]
        root_cause = _extract_conclusion(answer)
        remedy = _extract_remedy(answer)
        if not symptom or not root_cause:
            return None
        confidence = confidence if confidence is not None else (
            1.0 if source == "human" else CONFIDENCE_MODEL
        )
        vec = _embed_one(_memory_text(symptom, root_cause, remedy))
        if vec is None:
            return None   # 无向量则检索不可达，写入无意义
        with Session(engine) as session:
            dup = session.execute(
                text("SELECT id FROM knowledge_memories WHERE system_id = :sid AND symptom = :sym LIMIT 1"),
                {"sid": system_id, "sym": symptom},
            ).first()
            if dup:
                return None
            qv = "[" + ",".join(f"{x:.7g}" for x in vec) + "]"
            session.execute(
                text(
                    """
                    INSERT INTO knowledge_memories
                        (org_id, system_id, symptom, root_cause, remedy, source, confidence,
                         validity, embedding, diagnosis_report_id, workflow_id)
                    VALUES
                        (:org, :sid, :sym, :rc, :rem, :src, :conf, :val, CAST(:qv AS vector),
                         :rid, :wf)
                    """
                ),
                {"org": org_id, "sid": system_id, "sym": symptom, "rc": root_cause[:2000],
                 "rem": remedy[:2000], "src": source, "conf": confidence, "val": validity,
                 "qv": qv, "rid": report_id, "wf": workflow_id},
            )
            session.commit()
            new_id = session.execute(
                text("SELECT id FROM knowledge_memories WHERE system_id = :sid AND symptom = :sym ORDER BY id DESC LIMIT 1"),
                {"sid": system_id, "sym": symptom},
            ).first()
            log.info("[memory] system=%s 记忆已写入 id=%s（source=%s, validity=%s）",
                     system_id, new_id[0] if new_id else "?", source, validity)
            return new_id[0] if new_id else None
    except Exception as exc:  # noqa: BLE001
        log.debug("[memory] 写入失败（不影响主流程）: %s", exc)
        return None


def link_memory_to_workflow(diagnosis_report_id: int, workflow_id: int) -> None:
    """工作流挂到诊断报告时，同步关联已存在的记忆条目（供回查回填）。"""
    from app.core.database import engine
    from sqlmodel import Session

    try:
        with Session(engine) as session:
            session.execute(
                text(
                    "UPDATE knowledge_memories SET workflow_id = :wf "
                    "WHERE diagnosis_report_id = :rid AND workflow_id IS NULL"
                ),
                {"wf": workflow_id, "rid": diagnosis_report_id},
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("[memory] 关联失败: %s", exc)


def mark_memory_by_workflow(workflow_id: int, *, recovered: bool) -> None:
    """回查结果自动回填记忆有效性（验证闭环）。

    recovered=True  → validity=confirmed_success, confidence 0.5→0.9（处置有效）
    recovered=False → validity=failed（下次检索直接排除，防错误经验循环放大）
    """
    from app.core.database import engine
    from sqlmodel import Session

    try:
        with Session(engine) as session:
            row = session.execute(
                text("SELECT id FROM knowledge_memories WHERE workflow_id = :wf LIMIT 1"),
                {"wf": workflow_id},
            ).first()
            if not row:
                return
            validity = VALIDITY_CONFIRMED if recovered else VALIDITY_FAILED
            confidence = CONFIDENCE_VERIFIED if recovered else 0.3
            session.execute(
                text(
                    "UPDATE knowledge_memories SET validity = :v, confidence = :c, updated_at = now() "
                    "WHERE workflow_id = :wf"
                ),
                {"v": validity, "c": confidence, "wf": workflow_id},
            )
            session.commit()
            log.info("[memory] 回查结果回填 workflow=%s validity=%s", workflow_id, validity)
    except Exception as exc:  # noqa: BLE001
        log.debug("[memory] 回填失败（不影响工作流）: %s", exc)


def search_memories(
    *,
    org_id: int,
    system_id: int,
    query: str,
    max_results: int = 3,
) -> list[dict]:
    """按置信度 × 有效性加权的记忆检索。

    failed 记忆直接排除；confirmed 权重 1.0；unconfirmed 权重 0.5。
    返回 [{symptom, root_cause, remedy, source, confidence, validity, score}]。
    """
    from app.core.database import engine
    from sqlmodel import Session

    try:
        vecs = _embed_one(query)
        if vecs is None:
            return []
        qv = "[" + ",".join(f"{x:.7g}" for x in vecs) + "]"
        with Session(engine) as session:
            rows = session.execute(
                text(
                    """
                    SELECT symptom, root_cause, remedy, source, confidence, validity,
                           1 - (embedding <=> CAST(:qv AS vector)) AS similarity
                    FROM knowledge_memories
                    WHERE system_id = :sid
                      AND validity != :failed
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:qv AS vector)
                    LIMIT :k
                    """
                ),
                {"qv": qv, "sid": system_id, "failed": VALIDITY_FAILED, "k": max_results * 3},
            ).all()
        hits = []
        for symptom, root_cause, remedy, source, confidence, validity, similarity in rows:
            conf = float(confidence or 0.5)
            weight = {"confirmed_success": 1.0, "unconfirmed": 0.6}.get(validity, 0.5)
            score = round(float(similarity or 0) * conf * weight, 4)
            if score < 0.15:
                continue
            hits.append({
                "symptom": symptom, "root_cause": root_cause, "remedy": remedy,
                "source": source, "confidence": conf, "validity": validity, "score": score,
            })
        hits.sort(key=lambda h: -h["score"])
        return hits[:max_results]
    except Exception as exc:  # noqa: BLE001
        log.debug("[memory] 检索失败: %s", exc)
        return []
