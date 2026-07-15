from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from xhbx_rag.config import ConfigError, RetrievalConfig
from xhbx_rag.evaluation.config import load_evaluation_config
from xhbx_rag.evaluation.dataset import load_chunk_catalog, load_dataset
from xhbx_rag.evaluation.judge import EvaluationJudgeAgent
from xhbx_rag.evaluation.metrics import summarize_results
from xhbx_rag.evaluation.models import EvaluationItem
from xhbx_rag.evaluation.reporting import (
    build_backfill_payload,
    safe_backfill,
    write_markdown_report,
)
from xhbx_rag.evaluation.runner import (
    EvaluationPreflightError,
    compute_run_fingerprint,
    preflight_docker_milvus,
    run_items,
    write_run_metadata,
)
from xhbx_rag.evaluation.workbook import WorkbookAdapter
from xhbx_rag.milvus_store import configured_collection_names


SCORING_VERSION = "qa-evaluation-v1"
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def select_items(
    items: Sequence[EvaluationItem],
    *,
    item_ids: Sequence[str] | None,
    limit: object,
) -> list[EvaluationItem]:
    selected = list(items)
    if item_ids:
        requested = {str(item_id).strip() for item_id in item_ids}
        known = {item.item_id for item in selected}
        missing = sorted(requested - known)
        if missing:
            raise ValueError("评测项ID不存在：" + "、".join(missing))
        selected = [item for item in selected if item.item_id in requested]
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit 必须是正整数")
        selected = selected[:limit]
    if not selected:
        raise ValueError("没有可执行的评测项")
    return selected


def run_evaluate_command(args: argparse.Namespace) -> int:
    try:
        _validate_arguments(args)
        dataset_path = Path(args.dataset)
        if not dataset_path.is_file():
            raise ValueError(f"评测集文件不存在：{dataset_path}")
        output_root = Path(args.output_dir)
        run_id = _resolve_run_id(args.resume)
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        adapter = WorkbookAdapter(run_dir)
        dataset_json = run_dir / "dataset.json"
        adapter.extract(dataset_path, dataset_json)
        all_items = load_dataset(dataset_json)
        items = select_items(
            all_items,
            item_ids=args.item_id,
            limit=args.limit,
        )
        retrieval_config = RetrievalConfig.from_env()
        judge_config = load_evaluation_config()
        collection_stats = preflight_docker_milvus(retrieval_config)
        input_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        fingerprint = compute_run_fingerprint(
            input_sha256=input_sha256,
            scoring_version=SCORING_VERSION,
            top_n=args.top_n,
            top_k=args.top_k,
            answer_model_name=retrieval_config.model_name,
            judge_model_name=judge_config.judge_model_name,
            same_model_judge=judge_config.same_model_judge,
            milvus_uri=retrieval_config.milvus_uri,
            collection_stats=collection_stats,
        )
        collections = configured_collection_names(retrieval_config)
        run_info = _run_info(
            run_id=run_id,
            dataset_path=dataset_path,
            input_sha256=input_sha256,
            retrieval_config=retrieval_config,
            judge_model_name=judge_config.judge_model_name,
            same_model_judge=judge_config.same_model_judge,
            args=args,
            collection_stats=collection_stats,
        )
    except (ConfigError, EvaluationPreflightError, RuntimeError, ValueError) as exc:
        print(f"评测输入失败：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"评测落盘失败：{exc}", file=sys.stderr)
        return 3

    try:
        with EvaluationJudgeAgent(judge_config) as judge:
            results = run_items(
                items,
                judge=judge,
                run_dir=run_dir,
                fingerprint=fingerprint,
                resume=args.resume is not None,
                top_n=args.top_n,
                top_k=args.top_k,
                collections=collections,
                project_root=Path.cwd(),
                chunk_catalog=load_chunk_catalog(),
                concurrency=args.concurrency,
                judge_concurrency=args.judge_concurrency,
                config_payload=run_info,
            )
        summary = summarize_results(results)
        metadata = {**run_info, "汇总指标": summary}
        write_run_metadata(
            run_dir,
            fingerprint=fingerprint,
            config_payload=metadata,
        )
        write_markdown_report(run_dir, run_info, summary, results)
        if not args.no_xlsx:
            payload = build_backfill_payload(run_info, summary, results)
            safe_backfill(dataset_path, run_dir, adapter, payload)
    except (ConfigError, EvaluationPreflightError, ValueError) as exc:
        print(f"评测输入或工作簿验证失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"评测运行或落盘失败：{_safe_error(exc)}", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "运行ID": run_id,
                "运行目录": str(run_dir),
                "总题数": summary.get("总题数", len(results)),
                "平均分": summary.get("平均分", 0),
                "保守通过率": summary.get("保守通过率", 0),
                "工作簿已回填": not args.no_xlsx,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _validate_arguments(args: argparse.Namespace) -> None:
    for name in ("concurrency", "judge_concurrency", "top_n", "top_k"):
        value = getattr(args, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须是正整数")
    if args.top_n > 100:
        raise ValueError("top_n 不能大于 100")
    if args.top_k > 20:
        raise ValueError("top_k 不能大于 20")
    if args.top_k > args.top_n:
        raise ValueError("top_k 不能大于 top_n")
    if args.limit is not None and (
        isinstance(args.limit, bool)
        or not isinstance(args.limit, int)
        or args.limit <= 0
    ):
        raise ValueError("limit 必须是正整数")


def _resolve_run_id(resume: str | None) -> str:
    if resume is not None:
        normalized = str(resume).strip()
        if _RUN_ID_PATTERN.fullmatch(normalized) is None:
            raise ValueError("resume 运行ID格式无效")
        return normalized
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _run_info(
    *,
    run_id: str,
    dataset_path: Path,
    input_sha256: str,
    retrieval_config: RetrievalConfig,
    judge_model_name: str,
    same_model_judge: bool,
    args: argparse.Namespace,
    collection_stats: dict[str, dict[str, bool | int]],
) -> dict[str, object]:
    return {
        "运行ID": run_id,
        "输入文件名": dataset_path.name,
        "输入SHA256": input_sha256,
        "Git提交": _git_commit(),
        "问答模型名": retrieval_config.model_name,
        "裁判模型名": judge_model_name,
        "同模型裁判": same_model_judge,
        "初检候选数": args.top_n,
        "最终证据数": args.top_k,
        "问答并发数": args.concurrency,
        "裁判并发数": args.judge_concurrency,
        "评分版本": SCORING_VERSION,
        "Docker Milvus地址": retrieval_config.milvus_uri,
        "知识集合统计": collection_stats,
    }


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "未知"
    return completed.stdout.strip() or "未知"


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return type(exc).__name__
    text = str(exc).strip()
    return text[:300] if text else type(exc).__name__
