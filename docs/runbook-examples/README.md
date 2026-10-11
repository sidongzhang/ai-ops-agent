# 规程（runbook）示例

规程是给诊断 agent 的**按需加载流程文档**（借鉴 Pi 的 `SKILL.md`「渐进披露」思路）：
诊断时只把「名称 + 描述」目录注入 system prompt，agent 判断相关时再用
`load_runbook(name)` 取完整步骤。

## 格式

带 YAML frontmatter 的 Markdown：

```markdown
---
name: 规程名称
description: 什么场景下用（一句话，会出现在目录里）
---

# 正文
1. 第一步……
2. 第二步……
```

## 存放位置

放在**目标系统**的知识库文档目录下：

```
backend/app/agent/diagnostics/knowledge/docs/<system_id>/runbooks/<名称>.md
```

（该目录是运行时数据、不入库；本目录下的示例供拷贝参考。）

## 内置工具（agent 侧）

| 工具 | 作用 |
|---|---|
| `list_archive_files` / `read_archive_file` / `fetch_archive_file` | 归档文件的列目录 / 读内容 / 取回下载 |
| `run_python_analysis` | 沙箱跑 Python 做分析、生成报告并回传下载链接 |
| `load_runbook` | 按名称加载规程完整步骤 |

## 示例

- `归档文件解读.md`：怎么查某次触发/爆发的归档目录、文件清单、每个文件是什么、怎么发回。
- `kafka-积压排查.md`：怎么判断 Kafka 是否积压、定位积压来源、区分"没消费"与"没数据"。
