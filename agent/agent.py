#!/usr/bin/env python3
"""
AI 运维 Agent —— ReAct 主循环（DeepSeek API，OpenAI 兼容格式）

已支持多系统：每次问答绑定一个 system_id（默认 demo），系统拓扑由注册表动态生成，
工具与知识库都按该系统隔离。
用法：
  python agent.py                                  # 交互模式（默认 demo）
  python agent.py "系统好像有问题，帮我检查一下"        # 单次问答
  python agent.py --system example_remote "检查一下"   # 指定系统
"""
import sys
import os
import json
import logging

from dotenv import load_dotenv
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, '.env'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from openai import OpenAI
from tools import TOOLS, execute_tool
from knowledge_base import get_relevant_context
from skills.skill_router import get_skill_context
from registry import get_system

logging.basicConfig(level=logging.WARNING)

API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
if not API_KEY:
    print("❌  错误：未设置 DEEPSEEK_API_KEY")
    print("   请在项目根目录的 .env 文件中填入：DEEPSEEK_API_KEY=sk-xxxx")
    sys.exit(1)

client = OpenAI(
    api_key=API_KEY,
    base_url='https://api.deepseek.com',
)


# ── 动态 System Prompt（按系统注册表生成拓扑）────────────────────────────────

_STATIC_TAIL = """

## 可用工具说明
| 工具 | 用途 |
|------|------|
| list_services | 查看当前系统所有已注册服务的状态（首选入手工具）|
| check_process | 确认某个服务的健康状态 |
| read_logs | 读取服务最新日志 |
| search_logs | 在日志中搜索关键词（ERROR、异常等）|
| get_metrics | Prometheus PromQL 查询（CPU/内存/自定义指标）|
| restart_service | 重启某个服务（仅平台托管系统可用）|
| query_database | MySQL SELECT 查询（数据条数、异常记录等）|
| get_kafka_status | Kafka topic 列表 + 消费者 lag（积压量）|
| query_redis | Redis 只读查询（INFO / DBSIZE / KEYS 等）|
| get_system_info | 主机 CPU、内存、磁盘、负载概览（仅平台托管系统）|
| check_port | 检测某个端口是否可达（排查网络问题）|
| docker_logs | 查看本机 Docker 容器日志（仅平台托管系统）|
| restart_docker | 重启已停止的 Docker 容器（仅平台托管系统）|
| http_check | 检查 HTTP 端点是否正常响应 |

## 工作原则
1. 先用 list_services 全面了解当前系统所有服务状态
2. 对异常服务：check_process 确认 → read_logs / search_logs 分析原因
3. 网络/端口问题：check_port 排查连通性，http_check 验证 HTTP 服务
4. 性能问题：get_system_info + get_metrics 分析资源瓶颈
5. 数据积压：get_kafka_status 检查 consumer lag
6. 诊断清楚后，若是平台托管系统可执行 restart_service / restart_docker 修复；
   若是远程接入系统（只读），给出明确的人工处置建议而非尝试远程修复
7. 修复后验证：check_process + query_database 确认数据恢复
8. 最后输出清晰的【问题报告】：发现的问题 → 根因分析 → 执行的操作/建议 → 当前状态

## 效率要求（重要）
- **每次回复尽量同时调用多个独立工具**，不要一次只调一个。例如诊断阶段可同时调 check_process + check_port + read_logs
- **不要重复调用已经有结果的工具**，已知信息直接用
- **验证阶段一次完成**：修复后同时调 check_process + query_database

用中文回复，专业简洁。"""


def build_system_prompt(system: dict) -> str:
    name = system.get('name') or system.get('id', '')
    lines = [f'你是一个智能运维助手（AI Ops Agent），负责监控和维护「{name}」这套系统。', '', '## 系统架构']

    for s in system.get('services', []):
        parts = [f"connector={s.get('connector', 'local')}"]
        for k in ('kind', 'health_url', 'url', 'host', 'port',
                  'log_path', 'log_file', 'container', 'selector', 'systemd_unit'):
            if s.get(k):
                parts.append(f"{k}={s[k]}")
        lines.append(f"- **{s.get('name', '')}**：{', '.join(parts)}")

    infra = system.get('infra', {}) or {}
    if infra:
        lines.append('')
        lines.append('## 基础设施连接')
        for key, cfg in infra.items():
            if isinstance(cfg, dict):
                shown = {k: v for k, v in cfg.items() if k != 'password' and v not in ('', None)}
                lines.append(f"- {key}: {shown}")

    mode = ('平台托管（运行在平台所在主机，可执行重启等写动作）' if system.get('local')
            else 'agentless 远程接入（只读监控，不可远程重启；修复需给人工建议）')
    lines.append('')
    lines.append(f'## 接入模式\n本系统为 {mode}。')

    return '\n'.join(lines) + _STATIC_TAIL


def _compose_system_content(question: str, system_id: str) -> str:
    system = get_system(system_id) or {'id': system_id}
    system_content = build_system_prompt(system)
    skill = get_skill_context(question)
    if skill:
        system_content += f'\n\n## 当前激活的 Skill（请按此步骤操作）\n{skill}'
    context = get_relevant_context(question, system_id)
    if context:
        system_content += f'\n\n## 相关知识库参考\n{context}'
    return system_content


def run_agent(question: str, system_id: str = 'demo'):
    messages = [
        {'role': 'system', 'content': _compose_system_content(question, system_id)},
        {'role': 'user', 'content': question},
    ]

    print(f'\n{"="*60}')
    print(f'系统: {system_id}    问题: {question}')
    print(f'{"="*60}')

    step = 0
    while True:
        response = client.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            tools=TOOLS,
            tool_choice='auto',
        )

        choice = response.choices[0]
        message = choice.message
        messages.append(message)

        if choice.finish_reason == 'stop':
            print(f'\n{"="*60}')
            print('【Agent 结论】')
            print('='*60)
            print(message.content or '')
            break

        if choice.finish_reason == 'tool_calls' and message.tool_calls:
            if message.content:
                print(f'\n[思考] {message.content}')

            for tool_call in message.tool_calls:
                step += 1
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments or '{}')
                print(f'\n[步骤 {step}] {name}({json.dumps(args, ensure_ascii=False)})')

                result = execute_tool(name, args, system_id)
                preview = result[:400] + '\n...' if len(result) > 400 else result
                print(f'[结果]\n{preview}')

                messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_call.id,
                    'content': result,
                })
        else:
            if message.content:
                print(message.content)
            break


def get_agent_response(question: str, system_id: str = 'demo', on_step=None) -> str:
    """供飞书机器人调用：运行 Agent 并返回最终结论字符串。"""
    return get_agent_response_stream(question, system_id=system_id, on_step=on_step)


def get_agent_response_stream(question: str, system_id: str = 'demo', on_chunk=None, on_step=None) -> str:
    """ReAct 主循环：工具调用阶段非流式（快），最终答案阶段触发 on_chunk。
    on_chunk(text): LLM 开始生成最终结论时触发一次。
    on_step(tool_name, args): 每批工具执行前触发（通知进度卡片）。
    """
    import concurrent.futures

    messages = [
        {'role': 'system', 'content': _compose_system_content(question, system_id)},
        {'role': 'user', 'content': question},
    ]

    while True:
        resp = client.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            tools=TOOLS,
            tool_choice='auto',
            stream=False,
        )
        choice = resp.choices[0]
        finish_reason = choice.finish_reason
        message = choice.message
        content = message.content or ''

        if finish_reason == 'stop':
            if on_chunk and content:
                on_chunk(content)
            return content or '分析完成，未发现异常。'

        if finish_reason == 'tool_calls' and message.tool_calls:
            tool_calls_list = [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {'name': tc.function.name, 'arguments': tc.function.arguments or ''}
                }
                for tc in message.tool_calls
            ]
            messages.append({
                'role': 'assistant',
                'content': content or None,
                'tool_calls': tool_calls_list,
            })

            parsed = []
            for tc in tool_calls_list:
                name = tc['function']['name']
                try:
                    args = json.loads(tc['function']['arguments'] or '{}')
                except json.JSONDecodeError:
                    args = {}
                parsed.append((tc, name, args))

            if on_step and parsed:
                try:
                    on_step(parsed[0][1], parsed[0][2])
                except Exception:
                    pass

            def _run(item):
                tc, name, args = item
                return tc['id'], execute_tool(name, args, system_id)

            if len(parsed) == 1:
                tc, name, args = parsed[0]
                results_map = {tc['id']: execute_tool(name, args, system_id)}
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(parsed)) as ex:
                    results_map = {}
                    for tool_id, result in ex.map(_run, parsed):
                        results_map[tool_id] = result

            for tc, name, args in parsed:
                messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': results_map[tc['id']]})
        else:
            return content or '处理完成。'


def summarize_incident(question: str, result: str) -> str:
    """用轻量 LLM 调用把修复过程压缩成结构化摘要，供 runbook 归档。"""
    prompt = (
        "根据以下运维事件，提取关键信息填写模板，只输出模板内容，不要其他解释：\n\n"
        f"【触发问题】{question[:300]}\n\n【处理结果】{result[:800]}\n\n"
        "输出格式：\n"
        "**症状**: （一句话描述现象）\n"
        "**根因**: （一句话描述原因）\n"
        "**修复步骤**: （简要列出操作）\n"
        "**验证**: （恢复确认）\n"
        "**关键词**: （逗号分隔，便于搜索）"
    )
    resp = client.chat.completions.create(
        model='deepseek-chat',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=300,
    )
    return resp.choices[0].message.content.strip()


def main():
    args = sys.argv[1:]
    system_id = os.getenv('AGENT_SYSTEM_ID', 'demo')
    if args and args[0] == '--system':
        system_id = args[1] if len(args) > 1 else system_id
        args = args[2:]

    if args:
        run_agent(' '.join(args), system_id=system_id)
    else:
        print(f'🤖  AI 运维助手已启动（系统: {system_id}，输入 exit 退出）')
        print('示例问题：系统好像有问题，帮我检查一下')
        print('-' * 40)
        while True:
            try:
                q = input('\n你: ').strip()
                if q.lower() in ('exit', 'quit', '退出', 'q'):
                    print('再见！')
                    break
                if q:
                    run_agent(q, system_id=system_id)
            except (KeyboardInterrupt, EOFError):
                print('\n再见！')
                break


if __name__ == '__main__':
    main()
