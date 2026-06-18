#!/usr/bin/env python3
"""
AI 运维 Agent —— ReAct 主循环（DeepSeek API，OpenAI 兼容格式）
用法：
  python agent.py                         # 交互模式
  python agent.py "系统好像有问题，帮我检查一下"  # 单次问答
"""
import sys
import os
import json
import logging

from dotenv import load_dotenv
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, '.env'))

from openai import OpenAI
from tools import TOOLS, execute_tool
from knowledge_base import get_relevant_context
from skills.skill_router import get_skill_context

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

SYSTEM_PROMPT = """你是一个智能运维助手（AI Ops Agent），负责监控和维护一套分布式系统。

## 系统架构
- **Producer**：每10秒向 Kafka 发一条传感器数据（进程名 producer.py）
- **Consumer**：从 Kafka 消费数据，写入 MySQL（进程名 consumer.py）
- **Frontend**：Flask 展示数据统计，端口 5001（进程名 app.py）
- **Kafka**：消息队列（localhost:9092，topic: sensor-data）
- **MySQL**：数据库（localhost:3306，库: opsdb，表: sensor_data）
- **Redis**：缓存（localhost:6379）
- **Prometheus**：监控指标（http://localhost:9090）

## 可用工具说明
| 工具 | 用途 |
|------|------|
| list_services | 查看所有业务服务 + Docker 容器状态（首选入手工具）|
| check_process | 确认某个服务的进程是否存活 |
| read_logs | 读取服务最新日志 |
| search_logs | 在日志中搜索关键词（ERROR、异常等）|
| get_metrics | Prometheus PromQL 查询（CPU/内存/自定义指标）|
| restart_service | 重启 producer / consumer / frontend |
| query_database | MySQL SELECT 查询（数据条数、异常记录等）|
| get_kafka_status | Kafka topic 列表 + 消费者 lag（积压量）|
| query_redis | Redis 只读查询（INFO / DBSIZE / KEYS 等）|
| get_system_info | 主机 CPU、内存、磁盘、负载概览 |
| check_port | 检测某个端口是否可达（排查网络问题）|
| docker_logs | 查看 Docker 容器（kafka/mysql/redis）的日志 |
| http_check | 检查 HTTP 端点是否正常响应 |

## 工作原则
1. 先用 list_services 全面了解所有服务状态
2. 对异常服务：check_process 确认 → read_logs / search_logs 分析原因
3. 网络/端口问题：check_port 排查连通性，http_check 验证 HTTP 服务
4. 性能问题：get_system_info + get_metrics 分析资源瓶颈
5. 数据积压：get_kafka_status 检查 consumer lag
6. 诊断清楚后执行 restart_service 修复
7. 修复后验证：check_process + query_database 确认数据恢复
8. 最后输出清晰的【问题报告】：发现的问题 → 根因分析 → 执行的操作 → 当前状态

用中文回复，专业简洁。"""


def run_agent(question: str):
    context = get_relevant_context(question)
    skill = get_skill_context(question)
    system_content = SYSTEM_PROMPT
    if skill:
        system_content += f'\n\n## 当前激活的 Skill（请按此步骤操作）\n{skill}'
    if context:
        system_content += f'\n\n## 相关知识库参考\n{context}'

    messages = [
        {'role': 'system', 'content': system_content},
        {'role': 'user', 'content': question},
    ]

    print(f'\n{"="*60}')
    print(f'问题: {question}')
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

        # 把 assistant 消息加入历史
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

                result = execute_tool(name, args)
                preview = result[:400] + '\n...' if len(result) > 400 else result
                print(f'[结果]\n{preview}')

                messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_call.id,
                    'content': result,
                })
        else:
            # 意外情况，直接输出内容后退出
            if message.content:
                print(message.content)
            break


def get_agent_response(question: str, on_step=None) -> str:
    """供飞书机器人调用：运行 Agent 并返回最终结论字符串。
    on_step(tool_name, args): 每次执行工具前触发，可用于发送进度通知。
    """
    context = get_relevant_context(question)
    skill = get_skill_context(question)
    system_content = SYSTEM_PROMPT
    if skill:
        system_content += f'\n\n## 当前激活的 Skill（请按此步骤操作）\n{skill}'
    if context:
        system_content += f'\n\n## 相关知识库参考\n{context}'

    messages = [
        {'role': 'system', 'content': system_content},
        {'role': 'user', 'content': question},
    ]

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
            return message.content or '分析完成，未发现异常。'

        if choice.finish_reason == 'tool_calls' and message.tool_calls:
            for tool_call in message.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments or '{}')
                if on_step:
                    try:
                        on_step(name, args)
                    except Exception:
                        pass
                result = execute_tool(name, args)
                messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_call.id,
                    'content': result,
                })
        else:
            return message.content or '处理完成。'


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
    if len(sys.argv) > 1:
        run_agent(' '.join(sys.argv[1:]))
    else:
        print('🤖  AI 运维助手已启动（输入 exit 退出）')
        print('示例问题：系统好像有问题，帮我检查一下')
        print('-' * 40)
        while True:
            try:
                q = input('\n你: ').strip()
                if q.lower() in ('exit', 'quit', '退出', 'q'):
                    print('再见！')
                    break
                if q:
                    run_agent(q)
            except (KeyboardInterrupt, EOFError):
                print('\n再见！')
                break


if __name__ == '__main__':
    main()
