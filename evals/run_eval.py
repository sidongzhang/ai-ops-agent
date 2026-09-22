#!/usr/bin/env python
"""LLM Agent 离线评测脚手架（P0-1）。

用法（在仓库根目录执行）：
    backend/.venv/bin/python evals/run_eval.py --list
    backend/.venv/bin/python evals/run_eval.py --validate
    backend/.venv/bin/python evals/run_eval.py --dry-run          # 不调 LLM、不碰 Docker
    backend/.venv/bin/python evals/run_eval.py --run               # 真跑，调 diagnose_with_details
    backend/.venv/bin/python evals/run_eval.py --run --ablation all
    backend/.venv/bin/python evals/run_eval.py --inject redis-memory-001   # 需要 Docker
    backend/.venv/bin/python evals/run_eval.py --teardown redis-memory-001

详细说明见 evals/README.md。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import dataset as dataset_mod  # noqa: E402
import faults  # noqa: E402
import harness  # noqa: E402
import reporting  # noqa: E402

MODE_FLAGS = ("list_cases", "validate", "dry_run", "run", "inject", "teardown", "inject_all", "teardown_all")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_eval.py",
        description="多租户智能运维平台 · LLM Agent 离线评测脚手架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(__doc__ or "").split("用法（在仓库根目录执行）：")[-1],
    )
    parser.add_argument("--list", action="store_true", dest="list_cases", help="列出用例与故障类型统计")
    parser.add_argument("--validate", action="store_true", help="校验数据集 schema（不调用 LLM）")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="mock Agent 跑通全流程并产出报告")
    parser.add_argument("--run", action="store_true", dest="run", help="真实调用 diagnose_with_details")
    parser.add_argument("--ablation", choices=[*harness.ABLATION_GROUPS, "all"], default="full",
                        help="消融组，默认 full；all=依次跑 baseline/playbook/rag/full")
    parser.add_argument("--inject", metavar="CASE_ID", help="注入指定用例的故障（需要 Docker）")
    parser.add_argument("--teardown", metavar="CASE_ID", help="恢复指定用例的故障（需要 Docker）")
    parser.add_argument("--inject-all", action="store_true", dest="inject_all", help="注入筛选出的全部用例")
    parser.add_argument("--teardown-all", action="store_true", dest="teardown_all", help="恢复筛选出的全部用例")
    parser.add_argument("--fault-type", action="append", choices=list(dataset_mod.FAULT_TYPES),
                        help="只跑某类故障（可重复）")
    parser.add_argument("--case-id", action="append", help="只跑某个用例 id（可重复）")
    parser.add_argument("--limit", type=int, default=0, help="最多跑前 N 条用例（0=不限）")
    parser.add_argument("--model", default="", help="模型名覆盖（会自动把 model_mode 切到 api）")
    parser.add_argument("--model-mode", default="auto", choices=["auto", "default", "api", "local", "advanced"],
                        help="模型路由模式，默认 auto")
    parser.add_argument("--concurrency", type=int, default=1, help="并发用例数（默认 1；>1 为实验性）")
    parser.add_argument("--timeout", type=float, default=300.0, help="单用例超时秒数（默认 300）")
    parser.add_argument("--out-dir", default="", help="报告输出目录（默认 evals/results/<timestamp>/）")
    parser.add_argument("--dataset", default="", help="数据集 JSONL 路径")
    parser.add_argument("--descriptors-dir", default="", help="descriptor fixture 目录")
    parser.add_argument("--docs-root", default="", help="知识库 docs 根目录（默认 evals/knowledge/docs）")
    parser.add_argument("--journal", default="", help="故障注入 journal 路径（默认 evals/results/injections.jsonl）")
    parser.add_argument("--rag-mode", choices=["keyword", "auto"], default="",
                        help="RAG 检索模式：keyword=强制关键词（离线、确定性），auto=允许 embedding API")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印每条用例的进度与结果摘要")
    return parser


def _resolve_cases(args, cases: list[dict]) -> list[dict]:
    return dataset_mod.select_cases(
        cases,
        fault_types=args.fault_type,
        case_ids=args.case_id,
        limit=args.limit or None,
    )


def _load_cases(args) -> list[dict]:
    return dataset_mod.load_cases(args.dataset or dataset_mod.DEFAULT_DATASET)


def _run_one(case: dict, group: str, options: harness.RunOptions, timeout_s: float) -> dict:
    """带超时的单用例执行；超时不会杀掉线程（Python 限制），但会记为 error 并继续。"""
    if options.dry_run or timeout_s <= 0:
        return harness.run_case(case, group, options)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(harness.run_case, case, group, options)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            record = harness.base_record(case, group, options)
            record["error"] = f"TimeoutError: 单用例超过 {timeout_s:g}s 未返回"
            return record
        except Exception as exc:  # noqa: BLE001
            record = harness.base_record(case, group, options)
            record["error"] = f"{type(exc).__name__}: {exc}"
            return record


def cmd_list(args, cases: list[dict]) -> int:
    print(dataset_mod.format_case_table(cases))
    print()
    print(dataset_mod.format_distribution(cases))
    journal = faults.journal_summary(args.journal or faults.DEFAULT_JOURNAL)
    print()
    print(f"注入 journal: {journal['path']}")
    print(f"当前处于「已注入」状态的用例: {sorted(journal['injected']) or '无'}")
    return 0


def cmd_validate(args, cases: list[dict]) -> int:
    errors = dataset_mod.validate_cases(cases)
    print(dataset_mod.format_distribution(cases))
    print()
    if errors:
        print(f"❌ 数据集校验失败，共 {len(errors)} 个问题:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("✅ 数据集 schema 校验通过")
    return 0


def cmd_faults(args, cases: list[dict]) -> int:
    explicit_id = args.inject or args.teardown
    if explicit_id:
        try:
            selected = dataset_mod.select_cases(cases, case_ids=[explicit_id])
        except dataset_mod.DatasetError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
    else:
        selected = _resolve_cases(args, cases)
    if not selected:
        print("没有匹配的用例。", file=sys.stderr)
        return 1

    try:
        if args.inject or args.inject_all:
            records = faults.inject_all(selected, journal_path=args.journal or faults.DEFAULT_JOURNAL)
            failed = [record for record in records if not record.get("ok")]
            print(f"注入完成：{len(records) - len(failed)}/{len(records)} 成功")
            return 1 if failed else 0
        records = faults.teardown_all(selected, journal_path=args.journal or faults.DEFAULT_JOURNAL)
        failed = [record for record in records if not record.get("ok")]
        print(f"恢复完成：{len(records) - len(failed)}/{len(records)} 成功")
        return 1 if failed else 0
    except faults.DockerUnavailable as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except faults.FaultInjectionError as exc:
        print(f"❌ 故障注入失败: {exc}", file=sys.stderr)
        return 3


def cmd_eval(args, cases: list[dict]) -> int:
    if args.concurrency > 1:
        print("[warn] --concurrency>1 为实验性：pydantic-ai run_sync 与共享 Agent 实例并非线程安全。", file=sys.stderr)
    if not args.dry_run and not args.model:
        print(
            "[info] 未指定 --model，将由 pick_model(question, model_mode=auto) 路由"
            "（命中 P0/崩溃 等关键词可能升档到 advanced_agent_model）。",
            file=sys.stderr,
        )

    selected = _resolve_cases(args, cases)
    if not selected:
        print("没有匹配的用例。", file=sys.stderr)
        return 1

    groups = list(harness.ABLATION_GROUPS) if args.ablation == "all" else [args.ablation]
    options = harness.RunOptions(
        dry_run=args.dry_run,
        model=args.model,
        model_mode=args.model_mode,
        descriptor_dir=Path(args.descriptors_dir) if args.descriptors_dir else dataset_mod.DEFAULT_DESCRIPTOR_DIR,
        docs_root=Path(args.docs_root) if args.docs_root else dataset_mod.DEFAULT_DOCS_ROOT,
        keyword_only_rag=(args.rag_mode == "keyword") or (not args.rag_mode and args.dry_run),
        progress_echo=(lambda message: print(f"    {message}")) if args.verbose else None,
    )

    out_dir = Path(args.out_dir) if args.out_dir else reporting.default_out_dir(dataset_mod.DEFAULT_RESULTS_DIR)
    journal = Path(args.journal) if args.journal else faults.DEFAULT_JOURNAL
    injection = faults.journal_summary(journal)["injected"]
    docs_info = harness.docs_root_info(options.docs_root)

    tasks = [(case, group) for case in selected for group in groups]
    records: list[dict] = []
    total = len(tasks)
    done = 0
    print(f"开始评测：{len(selected)} 条用例 × {len(groups)} 个消融组 = {total} 次运行"
          f"（{'dry-run mock' if options.dry_run else '真实 LLM'}）")

    def _handle(record: dict) -> None:
        nonlocal done
        done += 1
        mark = "error" if record.get("error") else "ok"
        print(f"  [{done}/{total}] {record['case_id']} · {record['ablation']} · {mark} "
              f"({record.get('duration_ms', 0)}ms, {len(record.get('tool_calls') or [])} 次工具调用)")
        if args.verbose:
            if record.get("error"):
                print(f"      error: {record['error']}")
            else:
                first_line = (record.get("answer") or "").splitlines()[:1]
                print(f"      answer: {first_line[0] if first_line else '(空)'}")

    if args.concurrency > 1:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(_run_one, case, group, options, args.timeout) for case, group in tasks]
            records = [future.result() for future in futures]
    else:
        records = [_run_one(case, group, options, args.timeout) for case, group in tasks]

    for record in records:
        _handle(record)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": options.dry_run,
        "mode": "dry_run" if options.dry_run else "run",
        "harness": "evals/run_eval.py",
        "dataset_path": str(Path(args.dataset) if args.dataset else dataset_mod.DEFAULT_DATASET),
        "case_count": len(selected),
        "descriptor_dir": str(options.descriptor_dir),
        "docs_root": docs_info["docs_root"],
        "docs": docs_info["docs"],
        "doc_count": docs_info["doc_count"],
        "rag_mode": "keyword（强制离线，确定性）" if options.keyword_only_rag else "auto（允许 embedding API）",
        "model_override": args.model,
        "model_mode": args.model_mode,
        "concurrency": args.concurrency,
        "timeout_s": args.timeout,
        "ablation_groups": groups,
        "ablation_descriptions": harness.ABLATION_DESCRIPTIONS,
        "injection_note": f"journal 中仍标记为已注入的用例: {sorted(injection) or '无'}",
        "proxy_notes": harness.proxy_notes(),
        "fault_type_filter": args.fault_type or [],
        "case_id_filter": args.case_id or [],
        "command": " ".join(sys.argv),
    }

    try:
        result = reporting.write_report(records, selected, meta, out_dir)
    except Exception:  # noqa: BLE001 - 报告失败要给出完整上下文
        traceback.print_exc()
        return 4

    summary = result["summary"]
    overall = summary["overall"]
    print()
    print(f"✅ 报告已生成: {result['out_dir']}")
    print(f"   - {result['summary_json']}")
    print(f"   - {result['summary_md']}")
    print(f"   - {result['trajectories']}")
    print()
    print(
        "总体（{group}）：成功率 {succ}% · RCA {rca} · Tool-F1 {f1} · 证据覆盖率 {evi}% · "
        "幻觉率 {hal}% · 平均工具调用 {calls} · 平均耗时 {ms}ms".format(
            group=summary["meta"]["primary_group"],
            succ=overall.get("task_success_rate"),
            rca=overall.get("rca_accuracy"),
            f1=overall.get("tool_selection_f1"),
            evi=overall.get("evidence_coverage_rate"),
            hal=overall.get("hallucination_rate"),
            calls=overall.get("avg_tool_calls"),
            ms=overall.get("avg_duration_ms"),
        )
    )
    if overall.get("errors"):
        print(f"   注意：{overall['errors']} 条用例运行异常，已记录在报告里（未中断整批）。", file=sys.stderr)
    if options.dry_run:
        print("   ⚠️ dry-run 数字来自 mock Agent，是合成数据，只证明链路可用。", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    modes = [flag for flag in MODE_FLAGS if getattr(args, flag)]
    if not modes:
        parser.print_help()
        return 1
    if len(modes) > 1:
        print(f"❌ 一次只能选一个动作，收到: {modes}", file=sys.stderr)
        return 1

    boot = harness.bootstrap()  # 必须早于任何 app.* import
    if boot["proxy_notes"]:
        print(f"[info] 环境修正: {'；'.join(boot['proxy_notes'])}", file=sys.stderr)

    try:
        cases = _load_cases(args)
    except dataset_mod.DatasetError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    if args.validate:
        return cmd_validate(args, cases)
    if args.list_cases:
        return cmd_list(args, cases)

    errors = dataset_mod.validate_cases(cases)
    if errors:
        print(f"❌ 数据集未通过校验（{len(errors)} 个问题），先修数据集再评测:", file=sys.stderr)
        for error in errors[:12]:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if args.inject or args.teardown or args.inject_all or args.teardown_all:
        return cmd_faults(args, cases)
    return cmd_eval(args, cases)


if __name__ == "__main__":
    sys.exit(main())
