"""
飞书 API 客户端：获取 token、发送消息、回复、删除、添加 reaction
"""
import json
import re
import time
import requests
import logging

logger = logging.getLogger(__name__)
FEISHU_API = 'https://open.feishu.cn/open-apis'


# ── Markdown → Feishu Card 解析器 ────────────────────────────────────────────

def _parse_blocks(text: str) -> list:
    """把 markdown 文本切成块：('text', str) 或 ('table', [行列表])"""
    blocks = []
    lines = text.split('\n')
    i = 0
    buf = []

    while i < len(lines):
        line = lines[i]
        is_table_header = (
            '|' in line
            and i + 1 < len(lines)
            and re.match(r'^\s*\|[\s\-:|]+\|\s*$', lines[i + 1])
        )
        if is_table_header:
            if buf:
                blocks.append(('text', '\n'.join(buf)))
                buf = []
            table = [line]
            i += 2  # 跳过分隔行
            while i < len(lines) and '|' in lines[i]:
                table.append(lines[i])
                i += 1
            blocks.append(('table', table))
        else:
            buf.append(line)
            i += 1

    if buf:
        blocks.append(('text', '\n'.join(buf)))

    return blocks


def _build_table_as_column_set(lines: list) -> list:
    """
    Markdown 表格 → column_set 布局（支持长文本自动换行）
    表头灰底加粗，数据行交替白/浅灰，紧凑无多余间距
    """
    headers = [c.strip() for c in lines[0].split('|') if c.strip()]
    if not headers:
        return []

    n = len(headers)
    weights = [1] + [2] * (n - 1) if n > 1 else [1]

    def make_row(cells, bg='default', bold=False):
        cols = []
        for idx, cell in enumerate(cells[:n]):
            clean = re.sub(r'[\*`]', '', cell).strip()
            content = f'**{clean}**' if bold else cell
            cols.append({
                'tag': 'column',
                'width': 'weighted',
                'weight': weights[idx],
                'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}}],
            })
        return {
            'tag': 'column_set',
            'flex_mode': 'none',
            'background_style': bg,
            'columns': cols,
        }

    elements = []
    # 表头：灰底加粗
    elements.append(make_row(headers, bg='grey', bold=True))

    # 数据行：隔行浅灰，紧凑排列
    for i, line in enumerate(lines[1:]):
        cells = [c.strip() for c in line.split('|') if c.strip()]
        if not cells:
            continue
        bg = 'grey' if i % 2 == 1 else 'default'
        elements.append(make_row(cells, bg=bg))

    return elements


def _clean_text_block(text: str) -> str:
    """把 lark_md 不支持的语法转换掉"""
    text = re.sub(r'^#{1,6}\s+(.+)$', r'**\1**', text, flags=re.MULTILINE)
    text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_alert_card(issues: list, suggestion: str) -> dict:
    """构建简短告警卡片：列出异常 + 一句建议 + 立即修复按钮"""
    issue_lines = '\n'.join(f'❌ {i}' for i in issues)
    body = f'{issue_lines}\n\n**建议操作：** {suggestion}'

    elements = [
        {
            'tag': 'div',
            'text': {'tag': 'lark_md', 'content': body},
        },
        {'tag': 'hr'},
        {
            'tag': 'action',
            'actions': [
                {
                    'tag': 'button',
                    'text': {'tag': 'plain_text', 'content': '🔧 立即修复'},
                    'type': 'danger',
                    'value': {'action': 'fix_issues'},
                },
                {
                    'tag': 'button',
                    'text': {'tag': 'plain_text', 'content': '忽略'},
                    'type': 'default',
                    'value': {'action': 'dismiss', 'issues': issues},
                },
            ],
        },
        {
            'tag': 'note',
            'elements': [{'tag': 'plain_text', 'content': 'AI Ops Agent · 定时巡检 · Powered by DeepSeek'}],
        },
    ]

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': '⚠️  系统异常告警'},
            'template': 'red',
        },
        'elements': elements,
    }


def _build_fix_result_card(result: str) -> dict:
    """构建修复结果卡片（绿色头部）"""
    blocks = _parse_blocks(result)
    elements = []

    for block_type, content in blocks:
        if block_type == 'table':
            table_els = _build_table_as_column_set(content)
            if table_els:
                elements.append({'tag': 'hr'})
                elements.extend(table_els)
        else:
            cleaned = _clean_text_block(content)
            if cleaned:
                elements.append({
                    'tag': 'div',
                    'text': {'tag': 'lark_md', 'content': cleaned},
                })

    elements.append({'tag': 'hr'})
    elements.append({
        'tag': 'note',
        'elements': [{'tag': 'plain_text', 'content': 'AI Ops Agent · 修复完成 · Powered by DeepSeek'}],
    })

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': '✅  修复结果报告'},
            'template': 'green',
        },
        'elements': elements,
    }


def _build_claude_card(text: str) -> dict:
    """把 Agent 回复构建成 Claude 风格飞书卡片"""
    blocks = _parse_blocks(text)
    elements = []

    for block_type, content in blocks:
        if block_type == 'table':
            table_els = _build_table_as_column_set(content)
            if table_els:
                elements.append({'tag': 'hr'})
                elements.extend(table_els)
        else:
            cleaned = _clean_text_block(content)
            if cleaned:
                elements.append({
                    'tag': 'div',
                    'text': {'tag': 'lark_md', 'content': cleaned},
                })

    elements.append({'tag': 'hr'})
    elements.append({
        'tag': 'note',
        'elements': [{'tag': 'plain_text', 'content': 'AI Ops Agent · Powered by DeepSeek'}],
    })

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': '🤖  AI 运维分析'},
            'template': 'indigo',
        },
        'elements': elements,
    }


# ── 飞书客户端 ────────────────────────────────────────────────────────────────

class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str = ''
        self._token_expire: float = 0

    def _get_token(self) -> str:
        if time.time() < self._token_expire - 60:
            return self._token
        r = requests.post(
            f'{FEISHU_API}/auth/v3/tenant_access_token/internal',
            json={'app_id': self.app_id, 'app_secret': self.app_secret},
            timeout=10,
        )
        data = r.json()
        if data.get('code') != 0:
            raise RuntimeError(f"获取飞书 Token 失败: {data}")
        self._token = data['tenant_access_token']
        self._token_expire = time.time() + data.get('expire', 7200)
        return self._token

    def _headers(self):
        return {
            'Authorization': f'Bearer {self._get_token()}',
            'Content-Type': 'application/json',
        }

    def _post_message(self, chat_id: str, msg_type: str, content) -> dict:
        payload = {
            'receive_id': chat_id,
            'msg_type': msg_type,
            'content': json.dumps(content, ensure_ascii=False),
        }
        r = requests.post(
            f'{FEISHU_API}/im/v1/messages',
            params={'receive_id_type': 'chat_id'},
            headers=self._headers(),
            json=payload,
            timeout=10,
        )
        result = r.json()
        if result.get('code') != 0:
            logger.error(f"发送消息失败: {result}")
        return result

    def update_alert_card(self, message_id: str, state: str, body: str = ''):
        """原地更新告警卡片状态。state: 'fixed' | 'dismissed'"""
        if state == 'fixed':
            title, template = '✅  已修复', 'green'
        else:
            title, template = '⏭️  已忽略', 'grey'

        elements = []
        if body:
            elements.append({'tag': 'div', 'text': {'tag': 'lark_md', 'content': body}})
        elements.append({
            'tag': 'note',
            'elements': [{'tag': 'plain_text', 'content': 'AI Ops Agent · 定时巡检 · Powered by DeepSeek'}],
        })

        card = {
            'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': template},
            'elements': elements,
        }
        r = requests.patch(
            f'{FEISHU_API}/im/v1/messages/{message_id}',
            headers=self._headers(),
            json={'msg_type': 'interactive', 'content': json.dumps(card, ensure_ascii=False)},
            timeout=10,
        )
        result = r.json()
        if result.get('code') != 0:
            logger.warning(f'更新告警卡片失败（忽略）: {result}')

    def reply_text(self, message_id: str, text: str) -> str:
        """回复指定消息（文字），返回新消息的 message_id"""
        r = requests.post(
            f'{FEISHU_API}/im/v1/messages/{message_id}/reply',
            headers=self._headers(),
            json={
                'msg_type': 'text',
                'content': json.dumps({'text': text}, ensure_ascii=False),
            },
            timeout=10,
        )
        result = r.json()
        if result.get('code') != 0:
            logger.warning(f"回复消息失败: {result}")
            return ''
        return result.get('data', {}).get('message_id', '')

    def delete_message(self, message_id: str):
        """撤回/删除机器人自己发的消息"""
        if not message_id:
            return
        r = requests.delete(
            f'{FEISHU_API}/im/v1/messages/{message_id}',
            headers=self._headers(),
            timeout=10,
        )
        result = r.json()
        if result.get('code') != 0:
            logger.warning(f"删除消息失败（忽略）: {result}")

    def add_reaction(self, message_id: str, emoji_type: str = 'THUMBSUP') -> str:
        """在用户消息上加 emoji reaction，返回 reaction_id 供后续删除"""
        r = requests.post(
            f'{FEISHU_API}/im/v1/messages/{message_id}/reactions',
            headers=self._headers(),
            json={'reaction_type': {'emoji_type': emoji_type}},
            timeout=10,
        )
        result = r.json()
        if result.get('code') != 0:
            logger.warning(f"添加 reaction 失败（忽略）: {result}")
            return ''
        return result.get('data', {}).get('reaction_id', '')

    def delete_reaction(self, message_id: str, reaction_id: str):
        """移除之前加的 reaction"""
        if not reaction_id:
            return
        r = requests.delete(
            f'{FEISHU_API}/im/v1/messages/{message_id}/reactions/{reaction_id}',
            headers=self._headers(),
            timeout=10,
        )
        result = r.json()
        if result.get('code') != 0:
            logger.warning(f"删除 reaction 失败（忽略）: {result}")

    def send_alert_card(self, chat_id: str, issues: list, suggestion: str):
        """发送简短异常告警卡片（含立即修复按钮）"""
        card = _build_alert_card(issues, suggestion)
        result = self._post_message(chat_id, 'interactive', card)
        if result.get('code') != 0:
            logger.warning(f'告警卡片发送失败: {result}')
            text = '⚠️ 系统异常：\n' + '\n'.join(f'• {i}' for i in issues)
            self._post_message(chat_id, 'text', {'text': text})
        return result

    def send_fix_result_card(self, chat_id: str, result: str):
        """发送修复结果卡片（绿色）"""
        card = _build_fix_result_card(result)
        r = self._post_message(chat_id, 'interactive', card)
        if r.get('code') != 0:
            logger.warning('修复结果卡片发送失败，降级为纯文本')
            self._post_message(chat_id, 'text', {'text': f'✅ 修复完成：\n{result}'})
        return r

    def send_text(self, chat_id: str, text: str):
        return self._post_message(chat_id, 'text', {'text': text})

    def send_card(self, chat_id: str, markdown: str):
        """发送 Claude 风格卡片，表格用 column_set 渲染支持换行"""
        card = _build_claude_card(markdown)
        result = self._post_message(chat_id, 'interactive', card)
        if result.get('code') != 0:
            logger.warning("卡片发送失败，降级为纯文本")
            clean = re.sub(r'^#{1,6}\s+', '', markdown, flags=re.MULTILINE)
            self._post_message(chat_id, 'text', {'text': clean})
        return result
