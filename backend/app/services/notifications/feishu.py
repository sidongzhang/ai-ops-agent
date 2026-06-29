"""飞书机器人客户端：告警推送 + AI 回复卡片 + 消息交互（reaction / 更新卡片）。"""
import json
import logging
import re
import time

import httpx

log = logging.getLogger(__name__)
_FEISHU_API = "https://open.feishu.cn/open-apis"


# ── Markdown → 飞书卡片构建器 ─────────────────────────────────────────────────

def _parse_blocks(text: str) -> list:
    """把 markdown 文本切成 ('text', str) 或 ('table', [行列表]) 块。"""
    blocks, buf, lines = [], [], text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        is_table = ("|" in line and i + 1 < len(lines)
                    and re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[i + 1]))
        if is_table:
            if buf:
                blocks.append(("text", "\n".join(buf)))
                buf = []
            table = [line]
            i += 2
            while i < len(lines) and "|" in lines[i]:
                table.append(lines[i])
                i += 1
            blocks.append(("table", table))
        else:
            buf.append(line)
            i += 1
    if buf:
        blocks.append(("text", "\n".join(buf)))
    return blocks


def _build_table_cols(lines: list) -> list:
    headers = [c.strip() for c in lines[0].split("|") if c.strip()]
    if not headers:
        return []
    n = len(headers)
    weights = [1] + [2] * (n - 1) if n > 1 else [1]

    def row(cells, bg="default", bold=False):
        cols = []
        for idx, cell in enumerate(cells[:n]):
            clean = re.sub(r"[\*`]", "", cell).strip()
            cols.append({
                "tag": "column", "width": "weighted", "weight": weights[idx],
                "elements": [{"tag": "div", "text": {"tag": "lark_md",
                    "content": f"**{clean}**" if bold else cell}}],
            })
        return {"tag": "column_set", "flex_mode": "none",
                "background_style": bg, "columns": cols}

    elements = [row(headers, bg="grey", bold=True)]
    for i, line in enumerate(lines[1:]):
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if cells:
            elements.append(row(cells, bg="grey" if i % 2 else "default"))
    return elements


def _build_ai_card(markdown: str) -> dict:
    """把 AI 回答构建成飞书交互卡片（支持表格换行）。"""
    elements = []
    for btype, content in _parse_blocks(markdown):
        if btype == "table":
            cols = _build_table_cols(content)
            if cols:
                elements.append({"tag": "hr"})
                elements.extend(cols)
        else:
            cleaned = re.sub(r"^#{1,6}\s+(.+)$", r"**\1**", content, flags=re.MULTILINE)
            cleaned = re.sub(r"^-{3,}$", "", cleaned, flags=re.MULTILINE).strip()
            if cleaned:
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": cleaned}})
    elements.append({"tag": "hr"})
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": "AIOps Platform · AI 智能运维"}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🤖  AI 运维分析"},
                   "template": "indigo"},
        "elements": elements,
    }


# ── FeishuClient ──────────────────────────────────────────────────────────────

class FeishuClient:
    """完整的飞书机器人客户端：告警、AI 回复、reaction、卡片更新。"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = ""
        self._token_expire = 0.0

    # ── 鉴权 ──────────────────────────────────────────────────────
    def _get_token(self) -> str:
        if time.time() < self._token_expire - 60:
            return self._token
        r = httpx.post(
            f"{_FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 Token 获取失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire = time.time() + data.get("expire", 7200)
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}",
                "Content-Type": "application/json"}

    # ── 发消息 ────────────────────────────────────────────────────
    def _post_msg(self, chat_id: str, msg_type: str, content) -> dict:
        resp = httpx.post(
            f"{_FEISHU_API}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={"receive_id": chat_id, "msg_type": msg_type,
                  "content": json.dumps(content, ensure_ascii=False)},
            timeout=10,
        )
        return resp.json()

    def send_text(self, chat_id: str, text: str) -> dict:
        return self._post_msg(chat_id, "text", {"text": text})

    def send_card(self, chat_id: str, markdown: str) -> None:
        """发送 AI 回答卡片；发送失败降级为纯文本。"""
        card = _build_ai_card(markdown)
        result = self._post_msg(chat_id, "interactive", card)
        if result.get("code") != 0:
            log.warning(f"[feishu] 卡片发送失败，降级纯文本: {result}")
            self.send_text(chat_id, re.sub(r"^#{1,6}\s+", "", markdown, flags=re.MULTILINE))

    # ── 告警卡片 ──────────────────────────────────────────────────
    def send_alert(self, chat_id: str, system_name: str, failed_services: list[str]) -> None:
        lines = ["**以下服务出现异常：**"] + [f"• ✗  {s}" for s in failed_services]
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text",
                                 "content": f"🚨  {system_name} 服务告警"},
                       "template": "red"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text",
                                              "content": "告警由 AIOps Platform 自动触发"}]},
            ],
        }
        result = self._post_msg(chat_id, "interactive", card)
        if result.get("code") != 0:
            log.warning(f"[feishu] 告警发送失败: {result}")
        else:
            log.info(f"[feishu] 告警推送 → {chat_id}")

    def send_test(self, chat_id: str, system_name: str) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "✅  通知配置测试"},
                       "template": "green"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"系统「**{system_name}**」的飞书告警已成功配置。\n告警触发时将推送到本群。"}}],
        }
        result = self._post_msg(chat_id, "interactive", card)
        if result.get("code") != 0:
            raise RuntimeError(f"测试消息发送失败: {result.get('msg', result)}")

    # ── 消息 reaction ─────────────────────────────────────────────
    def add_reaction(self, message_id: str, emoji_type: str = "OK") -> str:
        """给用户消息加 emoji，返回 reaction_id（用于后续删除）。"""
        r = httpx.post(
            f"{_FEISHU_API}/im/v1/messages/{message_id}/reactions",
            headers=self._headers(),
            json={"reaction_type": {"emoji_type": emoji_type}},
            timeout=10,
        )
        result = r.json()
        if result.get("code") != 0:
            log.debug(f"[feishu] add_reaction 失败（忽略）: {result}")
            return ""
        return result.get("data", {}).get("reaction_id", "")

    def delete_reaction(self, message_id: str, reaction_id: str) -> None:
        if not reaction_id:
            return
        httpx.delete(
            f"{_FEISHU_API}/im/v1/messages/{message_id}/reactions/{reaction_id}",
            headers=self._headers(), timeout=10,
        )

    # ── 更新卡片 ──────────────────────────────────────────────────
    def update_card(self, message_id: str, state: str, body: str = "") -> None:
        """原地更新已发送卡片的状态。state: 'fixed' | 'dismissed'"""
        title = "✅  已修复" if state == "fixed" else "⏭️  已忽略"
        template = "green" if state == "fixed" else "grey"
        elements = []
        if body:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body}})
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": "AIOps Platform · AI 智能运维"}]})
        card = {"config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title},
                           "template": template},
                "elements": elements}
        httpx.patch(
            f"{_FEISHU_API}/im/v1/messages/{message_id}",
            headers=self._headers(),
            json={"msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False)},
            timeout=10,
        )


# ── 向后兼容别名 ──────────────────────────────────────────────────────────────
FeishuAlerter = FeishuClient
