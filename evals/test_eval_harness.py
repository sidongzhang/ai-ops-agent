"""评测脚手架自测：不依赖 Docker、不调用真实 LLM。

运行：
    backend/.venv/bin/python -m pytest evals/ -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import dataset
import faults
import harness
import metrics
import reporting

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
VENV_PYTHON = str(REPO_ROOT / "backend" / ".venv" / "bin" / "python")
PYTHON = VENV_PYTHON if Path(VENV_PYTHON).exists() else sys.executable
RUN_EVAL = str(EVALS_DIR / "run_eval.py")

REQUIRED_METRIC_KEYS = (
    "task_success_rate",
    "rca_accuracy",
    "tool_selection_f1",
    "tool_param_validity",
    "evidence_coverage_rate",
    "avg_tool_calls",
    "redundant_call_rate",
    "hallucination_rate",
    "avg_duration_ms",
    "avg_total_tokens",
)


# ── 数据集 ────────────────────────────────────────────────────────────────

def test_dataset_schema_valid():
    cases = dataset.load_cases()
    assert dataset.validate_cases(cases) == []


def test_dataset_coverage_requirements():
    cases = dataset.load_cases()
    assert len(cases) >= dataset.MIN_CASES
    counts = dataset.fault_type_counts(cases)
    for fault_type in dataset.FAULT_TYPES:
        assert counts.get(fault_type, 0) >= dataset.MIN_CASES_PER_FAULT, fault_type
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    decoys = [case for case in cases if case["ground_truth"]["forbidden_conclusion"]]
    assert len(decoys) >= dataset.MIN_DECOY_CASES
    actions = {case["inject"]["action"] for case in cases}
    assert {"docker_exec", "docker_stop", "docker_start", "http", "manual"} <= actions


def test_dataset_rejects_invalid_cases():
    cases = dataset.load_cases()
    broken = json.loads(json.dumps(cases[:4]))
    broken[0]["ground_truth"].pop("evidence_keys")
    broken[1]["ground_truth"]["required_tools"] = ["no_such_tool"]
    broken[2]["inject"] = {"action": "rm_rf", "target": "x", "command": ""}
    broken[3]["difficulty"] = "impossible"
    broken[0]["id"] = broken[1]["id"]
    errors = dataset.validate_cases(broken)
    joined = "\n".join(errors)
    assert "ground_truth 缺少字段 evidence_keys" in joined
    assert "未知工具" in joined
    assert "inject.action='rm_rf'" in joined
    assert "difficulty='impossible'" in joined
    assert "id 重复" in joined


def test_select_cases_filters():
    cases = dataset.load_cases()
    only_redis = dataset.select_cases(cases, fault_types=["redis_memory"])
    assert only_redis and all(case["fault_type"] == "redis_memory" for case in only_redis)
    limited = dataset.select_cases(cases, limit=3)
    assert len(limited) == 3
    one = dataset.select_cases(cases, case_ids=["redis-memory-001"])
    assert [case["id"] for case in one] == ["redis-memory-001"]
    with pytest.raises(dataset.DatasetError):
        dataset.select_cases(cases, case_ids=["nope-001"])


# ── 指标 ──────────────────────────────────────────────────────────────────

def _call(tool: str, payload: dict | None = None, status: str = "success", output: str = "ok") -> dict:
    return {"tool": tool, "input": payload or {}, "status": status, "duration_ms": 5, "output": output}


def _case(**overrides) -> dict:
    case = {
        "id": "unit-001",
        "fault_type": "redis_memory",
        "difficulty": "easy",
        "system_id": "eval-docker-local",
        "question": "redis 是不是内存不够了",
        "ground_truth": {
            "root_cause": "maxmemory 太小",
            "evidence_keys": ["maxmemory", "evicted_keys"],
            "required_tools": ["run_redis_command"],
            "forbidden_conclusion": ["网络抖动"],
        },
    }
    case.update(overrides)
    return case


def _record(answer: str, calls: list[dict], **overrides) -> dict:
    record = {
        "answer": answer,
        "tool_calls": calls,
        "duration_ms": 1234,
        "total_tokens": 567,
        "model": "unit-model",
        "ablation": "full",
        "error": "",
    }
    record.update(overrides)
    return record


def test_metrics_all_correct():
    case = _case()
    record = _record(
        "**结论**：maxmemory 打满导致 evicted_keys 增长",
        [_call("run_redis_command", {"command": "INFO memory"})],
    )
    row = metrics.score_case(case, record)
    assert row["task_success"] == 1
    assert row["rca_accuracy"] == 1.0
    assert row["tool_f1"] == 1.0
    assert row["hallucination"] == 0
    assert row["evidence_coverage"] == 1.0
    assert row["duration_ms"] == 1234 and row["total_tokens"] == 567


def test_metrics_missing_required_tool():
    case = _case()
    record = _record("maxmemory 打满，evicted_keys 增长", [_call("check_service", {"service": "Redis"})])
    row = metrics.score_case(case, record)
    assert row["required_tool_hit"] is False
    assert row["task_success"] == 0
    assert row["tool_f1"] == 0.0
    assert row["missed_tools"] == ["run_redis_command"]
    assert row["evidence_coverage"] == 1.0
    assert "未命中必需工具" in "、".join(metrics.failure_reasons(row))


def test_metrics_forbidden_conclusion_hits():
    case = _case()
    record = _record(
        "可能是网络抖动导致，maxmemory 与 evicted_keys 都正常",
        [_call("run_redis_command", {"command": "INFO memory"})],
    )
    row = metrics.score_case(case, record)
    assert row["hallucination"] == 1
    assert row["task_success"] == 0
    assert row["hallucination_phrases"] == ["网络抖动"]


def test_metrics_redundant_calls():
    case = _case()
    calls = [
        _call("run_redis_command", {"command": "INFO memory"}),
        _call("run_redis_command", {"command": "INFO memory"}),
        _call("run_redis_command", {"command": "INFO stats"}),
    ]
    row = metrics.score_case(case, _record("maxmemory evicted_keys", calls))
    assert row["tool_call_count"] == 3
    assert row["redundant_call_rate"] == pytest.approx(1 / 3, abs=0.001)


def test_metrics_partial_evidence_credit():
    assert metrics.key_match_weight("evicted 在增长，但没提内存", "evicted_keys") == 0.5
    assert metrics.key_match_weight("used memory 7.98M", "used_memory") == 1.0
    assert metrics.key_match_weight("EXPLAIN type: ALL 全表扫描", "type=ALL") == 1.0
    assert metrics.key_match_weight("完全无关", "maxmemory") == 0.0
    assert metrics.rca_accuracy("evicted 在增长", ["maxmemory", "evicted_keys"]) == 0.25


def test_metrics_evidence_coverage_without_tools():
    case = _case()
    row = metrics.score_case(case, _record("maxmemory evicted_keys", []))
    assert row["evidence_coverage"] == 0.0
    assert row["tool_param_validity"] == 0.0


def test_metrics_error_record_scores_zero():
    case = _case()
    row = metrics.score_case(case, _record("", [], error="RuntimeError: 429 rate limit"))
    assert row["status"] == "error"
    assert row["task_success"] == 0
    assert row["evidence_coverage"] == 0.0


def test_metrics_tool_selection_precision_penalises_extra_tools():
    scores = metrics.tool_selection_scores(["a", "b"], ["a", "b", "c", "d"])
    assert scores["tool_precision"] == 0.5
    assert scores["tool_recall"] == 1.0
    assert scores["tool_f1"] == pytest.approx(0.667, abs=0.001)


def test_metrics_degraded_call_rate():
    calls = [
        _call("run_kafka_command", {}, output="离线评测不支持：需要 Docker"),
        _call("check_service", {}, output="Kafka: ✅"),
    ]
    assert metrics.degraded_call_rate(calls) == 0.5


def test_aggregate_contains_all_required_metrics():
    rows = [
        metrics.score_case(_case(), _record("maxmemory evicted_keys", [_call("run_redis_command")])),
        metrics.score_case(_case(), _record("", [], error="boom")),
    ]
    agg = metrics.aggregate(rows)
    for key in REQUIRED_METRIC_KEYS:
        assert key in agg, key
    assert agg["cases"] == 2
    assert agg["errors"] == 1
    assert agg["task_success_rate"] == 50.0
    assert agg["diagnosis_evidence_rate_pct"] == agg["evidence_coverage_rate"]


# ── 消融参数 ──────────────────────────────────────────────────────────────

def test_ablation_params_control_prompt_inputs():
    cases = {case["id"]: case for case in dataset.load_cases()}
    case = cases["kafka-lag-002"]
    descriptor = dataset.load_descriptor(case["fault_type"])
    kwargs = {"descriptor": descriptor, "docs_root": dataset.DEFAULT_DOCS_ROOT, "keyword_only_rag": True}

    baseline = harness.resolve_ablation(case, "baseline", **kwargs)
    assert baseline["skill_steps"] == "" and baseline["knowledge_context"] == ""

    playbook = harness.resolve_ablation(case, "playbook", **kwargs)
    assert playbook["skill_steps"] and playbook["knowledge_context"] == ""
    assert playbook["skill_name"]

    rag = harness.resolve_ablation(case, "rag", **kwargs)
    assert rag["skill_steps"] == "" and rag["knowledge_context"]

    full = harness.resolve_ablation(case, "full", **kwargs)
    assert full["skill_steps"] and full["knowledge_context"]

    with pytest.raises(ValueError):
        harness.resolve_ablation(case, "nope", **kwargs)


def test_docs_root_patch_points_to_evals_knowledge():
    from app.agent.diagnostics.knowledge import store

    harness.patch_knowledge_docs_root(dataset.DEFAULT_DOCS_ROOT, keyword_only=True)
    assert store.DOCS_ROOT == dataset.DEFAULT_DOCS_ROOT
    info = harness.docs_root_info()
    assert info["doc_count"] >= 2
    docs = store.list_docs(dataset.EVAL_SYSTEM_ID)
    assert any(name.endswith(".md") for name in docs)


# ── descriptor fixture ────────────────────────────────────────────────────

def test_descriptor_fixtures_match_builder_shape():
    for fault_type in dataset.FAULT_TYPES:
        descriptor = dataset.load_descriptor(fault_type)
        assert descriptor["id"] == dataset.EVAL_SYSTEM_ID
        assert descriptor["name"]
        assert descriptor["local"] is True
        assert descriptor["infra"]["prometheus_url"]
        assert descriptor["services"]
        for service in descriptor["services"]:
            assert service["name"] and service["connector"]
            assert isinstance(service["config"], dict)
        assert any(service["connector"] == "prometheus" for service in descriptor["services"])


def test_fault_specific_descriptor_details():
    redis = dataset.load_descriptor("redis_memory")
    assert any(
        service["connector"] == "tcp" and service["config"].get("port") == 6379
        for service in redis["services"]
    )
    kafka = dataset.load_descriptor("kafka_lag")
    assert any("kafka" in service["name"].lower() for service in kafka["services"])
    oom = dataset.load_descriptor("container_oom")
    assert any(service.get("kind") == "docker" for service in oom["services"])


def test_build_prompt_accepts_fixture_descriptors():
    from app.services.descriptors.prompt import build_prompt

    for fault_type in dataset.FAULT_TYPES:
        descriptor = dataset.load_descriptor(fault_type)
        prompt = build_prompt(descriptor)
        assert descriptor["name"] in prompt
        for service in descriptor["services"]:
            assert service["name"] in prompt
        assert "输出格式" in prompt


# ── remote_command 适配器 ─────────────────────────────────────────────────

def test_remote_command_health_check_uses_real_connectors():
    descriptor = dataset.load_descriptor("redis_memory")
    execute = harness.make_remote_command(descriptor)
    result = execute("health_check", {})
    assert result["ok"] is True
    names = {item["name"] for item in result["result"]}
    assert {"Redis", "API", "Prometheus"} <= names
    assert all({"name", "ok", "detail"} <= set(item) for item in result["result"])


def test_remote_command_reads_fixture_logs_offline():
    descriptor = dataset.load_descriptor("redis_memory")
    execute = harness.make_remote_command(descriptor)
    logs = execute("fetch_logs", {"service": "API", "lines": 20})
    assert logs["ok"] is True and "maxmemory" in logs["result"]
    hits = execute("search_logs", {"service": "API", "keyword": "evicted", "lines": 50})
    assert hits["ok"] is True and "evicted" in hits["result"]
    missing = execute("fetch_logs", {"service": "NoSuchService", "lines": 5})
    assert missing["ok"] is False


def test_remote_command_degrades_without_docker(monkeypatch):
    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    descriptor = dataset.load_descriptor("kafka_lag")
    execute = harness.make_remote_command(descriptor)
    blocked = execute("run_kafka_command", {"service": "Kafka", "command": "topics --list"})
    assert blocked["ok"] is False and "离线评测不支持" in blocked["result"]
    gated = execute("query_prometheus", {"service": "Prometheus", "query": "up"})
    assert "ok" in gated and "result" in gated
    unknown = execute("wipe_everything", {})
    assert unknown["ok"] is False and "离线评测不支持" in unknown["result"]


def test_remote_command_blocks_docker_backed_logs(monkeypatch):
    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    descriptor = dataset.load_descriptor("container_oom")
    execute = harness.make_remote_command(descriptor)
    logs = execute("fetch_logs", {"service": "Kafka", "lines": 20})
    assert logs["ok"] is False and "Docker" in logs["result"]


def test_remote_command_redis_and_kafka_safety_guards():
    descriptor = dataset.load_descriptor("redis_memory")
    execute = harness.make_remote_command(descriptor)
    denied = execute("run_redis_command", {"service": "Redis", "command": "FLUSHALL"})
    assert denied["ok"] is False and "安全限制" in denied["result"]
    kafka_denied = execute("run_kafka_command", {"service": "Kafka", "command": "topics --delete"})
    assert kafka_denied["ok"] is False and "安全限制" in kafka_denied["result"]


# ── 故障注入 ──────────────────────────────────────────────────────────────

def _docker_case() -> dict:
    return {
        "id": "unit-docker-001",
        "fault_type": "redis_memory",
        "inject": {"action": "docker_exec", "target": "ai-ops-agent-redis-1", "command": "redis-cli CONFIG SET maxmemory 8mb"},
        "setup": [],
        "teardown": ["redis-cli CONFIG SET maxmemory 0"],
    }


def test_docker_available_false_when_cli_missing(monkeypatch):
    def _boom(*args, **kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(faults.subprocess, "run", _boom)
    assert faults.docker_available() is False


def test_inject_raises_docker_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    with pytest.raises(faults.DockerUnavailable) as excinfo:
        faults.inject(_docker_case(), journal_path=tmp_path / "journal.jsonl", echo=None)
    assert "colima start" in str(excinfo.value)
    assert "docker compose up -d" in str(excinfo.value)


def test_teardown_raises_docker_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    with pytest.raises(faults.DockerUnavailable):
        faults.teardown(_docker_case(), journal_path=tmp_path / "journal.jsonl", echo=None)


def test_manual_injection_needs_no_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    case = {
        "id": "unit-manual-001",
        "fault_type": "container_oom",
        "inject": {"action": "manual", "target": "ai-ops-agent-kafka-1", "command": ""},
        "setup": ["调小容器内存上限"],
        "teardown": ["恢复内存上限"],
    }
    journal = tmp_path / "journal.jsonl"
    record = faults.inject(case, journal_path=journal, echo=None)
    assert record["ok"] is True and record["action"] == "manual"
    assert record["notes"] == ["调小容器内存上限"]
    rollback = faults.teardown(case, journal_path=journal, echo=None)
    assert rollback["ok"] is True and rollback["notes"] == ["恢复内存上限"]
    lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [line["phase"] for line in lines] == ["inject", "teardown"]
    assert faults.journal_summary(journal)["injected"] == {}


def test_inject_all_preflight_raises_for_mixed_cases(monkeypatch, tmp_path):
    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    with pytest.raises(faults.DockerUnavailable):
        faults.inject_all([_docker_case()], journal_path=tmp_path / "journal.jsonl", echo=None)


def test_cli_inject_without_docker_exits_nonzero_without_traceback(monkeypatch, tmp_path, capsys):
    import run_eval

    monkeypatch.setattr(faults, "docker_available", lambda *a, **k: False)
    code = run_eval.main(["--inject", "redis-memory-001", "--journal", str(tmp_path / "journal.jsonl")])
    captured = capsys.readouterr()
    assert code == 2
    assert "colima start" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "journal.jsonl").exists()


# ── CLI 端到端（无 Docker / 无 LLM） ───────────────────────────────────────

def _run_cli(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, RUN_EVAL, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_cli_validate_exit_zero():
    result = _run_cli("--validate")
    assert result.returncode == 0, result.stderr
    assert "用例总数" in result.stdout
    assert "schema 校验通过" in result.stdout


def test_cli_list_exit_zero():
    result = _run_cli("--list")
    assert result.returncode == 0, result.stderr
    assert "redis_memory" in result.stdout
    assert "注" in result.stdout  # journal 摘要行


def test_cli_dry_run_produces_report(tmp_path):
    out_dir = tmp_path / "dry"
    result = _run_cli("--dry-run", "--limit", "4", "--out-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    for name in ("summary.json", "summary.md", "trajectories.jsonl"):
        assert (out_dir / name).exists(), name

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["dry_run"] is True
    assert summary["meta"]["case_count"] == 4
    for key in REQUIRED_METRIC_KEYS:
        assert key in summary["overall"], key
    assert summary["cases"] and all(row["ablation"] == "full" for row in summary["cases"])
    assert summary["overall"]["avg_progress_events"] > 0, "on_progress 回调必须被统计到"
    assert "| 指标 | 值 | 说明 |" in (out_dir / "summary.md").read_text(encoding="utf-8")

    trajectories = [
        json.loads(line)
        for line in (out_dir / "trajectories.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(trajectories) == 4
    for record in trajectories:
        assert record["answer"]
        assert record["tool_calls"], "dry-run mock 必须产出 tool_calls 轨迹"
        assert record["events"], "dry-run mock 必须产出 progress 事件"


def test_cli_dry_run_ablation_all_writes_comparison_table(tmp_path):
    out_dir = tmp_path / "ablation"
    result = _run_cli(
        "--dry-run", "--ablation", "all", "--limit", "2",
        "--case-id", "redis-memory-001", "--case-id", "kafka-lag-002",
        "--out-dir", str(out_dir),
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["ablations"]) == set(harness.ABLATION_GROUPS)
    assert summary["meta"]["primary_group"] == "baseline"
    markdown = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "## 消融对比" in markdown
    for group in harness.ABLATION_GROUPS:
        assert f"| {group} " in markdown


def test_cli_dry_run_records_errors_without_crashing(tmp_path, monkeypatch):
    """单条用例失败不影响整批：把 descriptor 目录指到空目录，所有用例应记为 error 且退出码仍为 0。"""
    out_dir = tmp_path / "errors"
    empty = tmp_path / "empty-descriptors"
    empty.mkdir()
    result = _run_cli("--dry-run", "--limit", "3", "--descriptors-dir", str(empty), "--out-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["overall"]["errors"] == 3
    assert summary["overall"]["task_success_rate"] == 0.0
    assert all(row["status"] == "error" and row["error"] for row in summary["cases"])


def test_cli_conflicting_actions_rejected():
    result = _run_cli("--validate", "--dry-run")
    assert result.returncode == 1
    assert "一次只能选一个动作" in result.stderr


# ── 真实 Agent 链路（TestModel，无网络/无 LLM） ────────────────────────────

def test_real_agent_loop_runs_offline_with_test_model():
    """用 pydantic-ai TestModel 替换 pick_model，真实跑一遍 diagnose_with_details + 全部工具。"""
    pytest.importorskip("pydantic_ai.models.test")
    from pydantic_ai.models.test import TestModel

    from app.agent.diagnostics import runner

    case = next(item for item in dataset.load_cases() if item["id"] == "svc-unreachable-001")
    original = runner.pick_model
    runner.pick_model = lambda *args, **kwargs: TestModel(  # type: ignore[assignment]
        call_tools="all",
        custom_output_text=(
            "**结论**：API 进程未监听 8000 端口，connection refused（置信度：高）\n"
            "**影响**：网关全部 502\n"
            "**关键证据**：\n- 8000 端口无监听\n- 连接被拒绝\n"
            "**建议**：\n- 重启 API 进程\n"
            "**待确认**：无"
        ),
    )
    try:
        record = harness.run_case(
            case,
            "full",
            harness.RunOptions(
                dry_run=False,
                docs_root=dataset.DEFAULT_DOCS_ROOT,
                keyword_only_rag=True,
            ),
        )
    finally:
        runner.pick_model = original  # type: ignore[assignment]

    assert record["error"] == "", record["error"]
    assert record["answer"]
    assert record["tool_calls"], "TestModel(call_tools='all') 应该触发工具调用"
    row = metrics.score_case(case, record)
    assert row["task_success"] == 1
    assert row["evidence_hit_count"] >= 1
    assert row["hallucination"] == 0


# ── 报告 ──────────────────────────────────────────────────────────────────

def test_reporting_round_trip(tmp_path):
    cases = dataset.load_cases()[:2]
    records = [
        {
            "case_id": case["id"],
            "fault_type": case["fault_type"],
            "difficulty": case["difficulty"],
            "ablation": "full",
            "answer": " ".join(case["ground_truth"]["evidence_keys"]),
            "tool_calls": [{"tool": tool, "input": {}, "status": "success"} for tool in case["ground_truth"]["required_tools"]],
            "duration_ms": 10,
            "total_tokens": 20,
            "model": "unit",
            "events": [],
            "error": "",
        }
        for case in cases
    ]
    meta = {
        "dry_run": False,
        "ablation_groups": ["full"],
        "generated_at": "2026-01-01T00:00:00+00:00",
        "case_count": len(cases),
    }
    result = reporting.write_report(records, cases, meta, tmp_path / "r")
    summary = result["summary"]
    assert summary["overall"]["task_success_rate"] == 100.0
    assert summary["by_fault_type"]
    assert Path(result["summary_json"]).exists()
    assert Path(result["trajectories"]).exists()
