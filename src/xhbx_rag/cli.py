from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from .answer import AnswerAgent, answer_query
from .chunk_builder import build_chunks
from .config import RetrievalConfig
from .course_enrichment import CourseEnrichmentAgentScopeAgent
from .course_parser import parse_course_dir
from .embedding import EmbeddingClient
from .indexer import index_chunks
from .milvus_store import (
    MilvusStore,
    MultiCollectionStore,
    create_milvus_store,
    create_retrieval_store,
)
from .normalizer import normalize_case
from .observability import (
    CompositeTraceSink,
    JsonlTraceSink,
    TraceSink,
    close_trace,
    create_studio_trace_sink,
)
from .parser import ParseFatalError, parse_inputs
from .query_understanding import QueryUnderstandingAgent
from .rerank import RerankClient
from .report import build_parse_report
from .sales_generation import (
    SalesInsightAgentScopeAgent,
    VisionImageDescriptionAgent,
    generate_case_sales_insights_async,
)
from .search import search_evidence
from .writer import write_outputs


def _add_generate_insights_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--case-name", default=None)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--retry-attempts", type=int, default=5)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--max-section-chars", type=int, default=18000)
    parser.add_argument(
        "--section-concurrency",
        type=int,
        default=3,
        help="并发生成章节 sales_evidence 的任务数，默认 3",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="以流式方式调用大模型，便于规避长请求的网关空闲超时",
    )
    parser.add_argument(
        "--compact-case-input",
        action="store_true",
        help="整案汇总时使用精简后的章节证据，减少最终汇总请求长度",
    )
    parser.add_argument(
        "--reuse-section-evidence",
        action="store_true",
        help="输出目录中已有合法章节 sales_evidence.json 时直接复用，跳过该章节的模型抽取",
    )
    parser.add_argument(
        "--case-call-mode",
        choices=["split", "single"],
        default="split",
        help="案例级汇总方式：split 按知识类型拆成多次小调用（默认，支持断点续跑与部分成功），single 保留单次大调用",
    )
    thinking_group = parser.add_mutually_exclusive_group()
    parser.set_defaults(enable_thinking=True)
    thinking_group.add_argument(
        "--enable-thinking",
        dest="enable_thinking",
        action="store_true",
        help="生成销售洞察时启用模型思考模式；默认启用",
    )
    thinking_group.add_argument(
        "--no-thinking",
        dest="enable_thinking",
        action="store_false",
        help="生成销售洞察时关闭模型思考模式，用于网络不稳或快速调试",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="将步骤 trace 以 JSONL 写到 stderr",
    )
    parser.add_argument(
        "--studio",
        action="store_true",
        help="将步骤 trace 发送到 AgentScope Studio",
    )
    parser.add_argument(
        "--studio-endpoint",
        default="localhost:4317",
        help="AgentScope Studio OTLP gRPC endpoint，默认 localhost:4317",
    )


def _add_trace_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--trace",
        action="store_true",
        help="将步骤 trace 以 JSONL 写到 stderr",
    )
    parser.add_argument(
        "--studio",
        action="store_true",
        help="将步骤 trace 发送到 AgentScope Studio",
    )
    parser.add_argument(
        "--studio-endpoint",
        default="localhost:4317",
        help="AgentScope Studio OTLP gRPC endpoint，默认 localhost:4317",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhbx-rag",
        description="解析销售洞察文件并生成 RAG 入库前产物",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    parse_parser = subparsers.add_parser("parse", help="解析 case.sales_insights.json")
    parse_parser.add_argument("--insights", required=True, type=Path)
    parse_parser.add_argument("--playbook", type=Path, default=None)
    parse_parser.add_argument("--out", required=True, type=Path)

    generate_parser = subparsers.add_parser(
        "generate-insights",
        help="从案例素材目录生成 case.sales_insights.json 与 case.sales_playbook.md",
    )
    generate_parser.add_argument("--out", required=True, type=Path)
    _add_generate_insights_arguments(generate_parser)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="一键执行 generate-insights → parse → index，把案例素材直通向量库",
    )
    ingest_parser.add_argument(
        "--generated-out",
        type=Path,
        default=Path("generated"),
        help="generate-insights 输出目录，默认 generated",
    )
    ingest_parser.add_argument(
        "--parsed-out",
        type=Path,
        default=Path("parsed"),
        help="parse 输出目录，默认 parsed",
    )
    ingest_parser.add_argument(
        "--index-mode",
        choices=["incremental", "rebuild"],
        default="incremental",
        help="index 写入模式，默认 incremental",
    )
    _add_generate_insights_arguments(ingest_parser)

    index_parser = subparsers.add_parser("index", help="写入 chunks.jsonl 到 Milvus Lite")
    index_parser.add_argument("--chunks", required=True, type=Path)
    index_parser.add_argument(
        "--mode",
        choices=["incremental", "rebuild"],
        default="incremental",
        help="入库模式：incremental 使用 upsert 增量覆盖同 chunk_id；rebuild 清空 collection 后重新入库",
    )
    index_parser.add_argument(
        "--collection",
        choices=["case", "course"],
        default="case",
        help="目标 collection：case 写入案例库（默认），course 写入课程库",
    )
    index_parser.add_argument(
        "--trace",
        action="store_true",
        help="将步骤 trace 以 JSONL 写到 stderr",
    )
    index_parser.add_argument(
        "--studio",
        action="store_true",
        help="将步骤 trace 发送到 AgentScope Studio",
    )
    index_parser.add_argument(
        "--studio-endpoint",
        default="localhost:4317",
        help="AgentScope Studio OTLP gRPC endpoint，默认 localhost:4317",
    )

    parse_course_parser = subparsers.add_parser(
        "parse-course",
        help="解析培训课程目录并规则切块，产出 chunks.jsonl 与 parse_report.json",
    )
    parse_course_parser.add_argument("--course-dir", required=True, type=Path)
    parse_course_parser.add_argument(
        "--out",
        type=Path,
        default=Path("parsed_courses"),
        help="输出目录，默认 parsed_courses",
    )
    parse_course_parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="关闭课程级 LLM 摘要与打标（默认开启，失败自动降级为纯规则产物）",
    )
    _add_trace_arguments(parse_course_parser)

    ingest_course_parser = subparsers.add_parser(
        "ingest-course",
        help="一键执行 parse-course → index，把培训课程目录直通课程库",
    )
    ingest_course_parser.add_argument("--course-dir", required=True, type=Path)
    ingest_course_parser.add_argument(
        "--out",
        type=Path,
        default=Path("parsed_courses"),
        help="parse-course 输出目录，默认 parsed_courses",
    )
    ingest_course_parser.add_argument(
        "--index-mode",
        choices=["incremental", "rebuild"],
        default="incremental",
        help="index 写入模式，默认 incremental",
    )
    ingest_course_parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="关闭课程级 LLM 摘要与打标（默认开启，失败自动降级为纯规则产物）",
    )
    _add_trace_arguments(ingest_course_parser)

    search_parser = subparsers.add_parser("search", help="检索销售洞察 evidence chunks")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top-n", type=int, default=20)
    search_parser.add_argument("--top-k", type=int, default=5)
    search_parser.add_argument(
        "--trace",
        action="store_true",
        help="将步骤 trace 以 JSONL 写到 stderr",
    )
    search_parser.add_argument(
        "--studio",
        action="store_true",
        help="将步骤 trace 发送到 AgentScope Studio",
    )
    search_parser.add_argument(
        "--studio-endpoint",
        default="localhost:4317",
        help="AgentScope Studio OTLP gRPC endpoint，默认 localhost:4317",
    )

    answer_parser = subparsers.add_parser("answer", help="检索 evidence 并生成基于证据的回答")
    answer_parser.add_argument("--query", required=True)
    answer_parser.add_argument("--top-n", type=int, default=20)
    answer_parser.add_argument("--top-k", type=int, default=5)
    answer_parser.add_argument(
        "--trace",
        action="store_true",
        help="将步骤 trace 以 JSONL 写到 stderr",
    )
    answer_parser.add_argument(
        "--studio",
        action="store_true",
        help="将步骤 trace 发送到 AgentScope Studio",
    )
    answer_parser.add_argument(
        "--studio-endpoint",
        default="localhost:4317",
        help="AgentScope Studio OTLP gRPC endpoint，默认 localhost:4317",
    )
    return parser


def _write_fatal_report(
    out_dir: Path,
    insights_path: Path,
    playbook_path: Path | None,
    error: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "input_files": {
            "insights": str(insights_path),
            "playbook": str(playbook_path) if playbook_path is not None else None,
        },
        "output_files": {"report": str(out_dir / "parse_report.json")},
        "case_name": None,
        "counts": {},
        "warnings": [],
        "errors": [error],
    }
    (out_dir / "parse_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _cmd_parse(args: argparse.Namespace) -> int:
    try:
        parsed = parse_inputs(args.insights, args.playbook)
        knowledge = normalize_case(parsed)
        chunks = build_chunks(knowledge)
        report = build_parse_report(
            args.insights,
            args.playbook,
            args.out,
            knowledge,
            chunks,
            parsed.warnings,
        )
        write_outputs(args.out, knowledge, chunks, report)
    except ParseFatalError as exc:
        _write_fatal_report(args.out, args.insights, args.playbook, str(exc))
        return 1
    return 0


def _run_generate_insights(
    args: argparse.Namespace,
    config: RetrievalConfig,
    trace: TraceSink | None,
    output_dir: Path,
):
    agent = SalesInsightAgentScopeAgent(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model_name,
        timeout=args.timeout,
        retry_attempts=args.retry_attempts,
        retry_base_delay=args.retry_base_delay,
        max_section_chars=args.max_section_chars,
        enable_thinking=args.enable_thinking,
        stream=args.stream,
        compact_case_input=args.compact_case_input,
    )
    vision_agent = (
        VisionImageDescriptionAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.vision_model_name,
            timeout=args.timeout,
            retry_attempts=args.retry_attempts,
            retry_base_delay=args.retry_base_delay,
            stream=args.stream,
        )
        if config.vision_model_name
        else None
    )
    return asyncio.run(
        generate_case_sales_insights_async(
            case_dir=args.case_dir,
            output_dir=output_dir,
            case_name=args.case_name,
            section_agent=agent,
            case_agent=agent,
            vision_agent=vision_agent,
            trace=trace,
            section_concurrency=args.section_concurrency,
            reuse_section_evidence=args.reuse_section_evidence,
            case_call_mode=args.case_call_mode,
        )
    )


def _cmd_generate_insights(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.generate-insights")
    try:
        result = _run_generate_insights(args, config, trace, args.out)
    finally:
        close_trace(trace)
    print(json.dumps(_generation_result_payload(result), ensure_ascii=False, indent=2))
    return 0 if result.status in ("ok", "partial") else 1


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.ingest")
    summary: dict = {"generate": None, "parse": None, "index": None}
    try:
        result = _run_generate_insights(args, config, trace, args.generated_out)
        summary["generate"] = _generation_result_payload(result)
        if result.status not in ("ok", "partial") or result.insights_path is None:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1
        try:
            parsed = parse_inputs(result.insights_path, result.playbook_path)
            knowledge = normalize_case(parsed)
            chunks = build_chunks(knowledge)
            report = build_parse_report(
                result.insights_path,
                result.playbook_path,
                args.parsed_out,
                knowledge,
                chunks,
                parsed.warnings,
            )
            output_paths = write_outputs(args.parsed_out, knowledge, chunks, report)
        except ParseFatalError as exc:
            _write_fatal_report(
                args.parsed_out,
                result.insights_path,
                result.playbook_path,
                str(exc),
            )
            summary["parse"] = {"error": str(exc)}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1
        summary["parse"] = {
            "counts": report.counts,
            "warnings": report.warnings,
            "chunks_path": str(output_paths.chunks_path),
        }
        indexed = index_chunks(
            output_paths.chunks_path,
            _embedding_client(config),
            _milvus_store(config),
            trace=trace,
            mode=args.index_mode,
        )
        summary["index"] = {"indexed": indexed, "mode": args.index_mode}
    finally:
        close_trace(trace)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _embedding_client(config: RetrievalConfig) -> EmbeddingClient:
    return EmbeddingClient(
        base_url=config.embedding_base_url,
        api_key=config.embedding_api_key,
        model=config.embedding_model_name,
    )


def _milvus_store(config: RetrievalConfig, collection: str = "case") -> MilvusStore:
    if collection == "course":
        return create_milvus_store(
            config, collection_name=config.milvus_course_collection
        )
    return create_milvus_store(config)


def _retrieval_store(config: RetrievalConfig) -> MultiCollectionStore:
    return create_retrieval_store(config)


def _course_enrichment_agent(
    config: RetrievalConfig, no_enrich: bool
) -> CourseEnrichmentAgentScopeAgent | None:
    if no_enrich:
        return None
    return CourseEnrichmentAgentScopeAgent(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model_name,
    )


def _trace_sink(args: argparse.Namespace, root_name: str) -> TraceSink | None:
    sinks: list[TraceSink] = []
    if args.trace:
        sinks.append(JsonlTraceSink(sys.stderr))
    if args.studio:
        sinks.append(
            create_studio_trace_sink(
                endpoint=args.studio_endpoint,
                root_name=root_name,
            )
        )
    if not sinks:
        return None
    if len(sinks) == 1:
        return sinks[0]
    return CompositeTraceSink(sinks)


def _cmd_index(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.index")
    try:
        count = index_chunks(
            args.chunks,
            _embedding_client(config),
            _milvus_store(config, collection=getattr(args, "collection", "case")),
            trace=trace,
            mode=args.mode,
        )
    finally:
        close_trace(trace)
    print(json.dumps({"indexed": count}, ensure_ascii=False))
    return 0


def _cmd_parse_course(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.parse-course")
    try:
        report = parse_course_dir(
            args.course_dir,
            args.out,
            enrichment_agent=_course_enrichment_agent(config, args.no_enrich),
            trace=trace,
        )
    finally:
        close_trace(trace)
    print(report.to_json())
    return 0


def _cmd_ingest_course(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.ingest-course")
    summary: dict = {"parse": None, "index": None}
    try:
        report = parse_course_dir(
            args.course_dir,
            args.out,
            enrichment_agent=_course_enrichment_agent(config, args.no_enrich),
            trace=trace,
        )
        summary["parse"] = report.counts
        indexed = index_chunks(
            Path(report.output_files["chunks"]),
            _embedding_client(config),
            _milvus_store(config, collection="course"),
            trace=trace,
            mode=args.index_mode,
        )
        summary["index"] = {"indexed": indexed, "mode": args.index_mode}
    finally:
        close_trace(trace)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.search")
    try:
        result = search_evidence(
            query=args.query,
            query_agent=QueryUnderstandingAgent(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model_name,
            ),
            embedding_client=_embedding_client(config),
            store=_retrieval_store(config),
            reranker=RerankClient(
                base_url=config.rerank_base_url,
                api_key=config.rerank_api_key,
                model=config.rerank_model_name,
            ),
            top_n=args.top_n,
            top_k=args.top_k,
            trace=trace,
        )
    finally:
        close_trace(trace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_answer(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    trace = _trace_sink(args, "xhbx-rag.answer")
    try:
        result = answer_query(
            query=args.query,
            query_agent=QueryUnderstandingAgent(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model_name,
            ),
            embedding_client=_embedding_client(config),
            store=_retrieval_store(config),
            reranker=RerankClient(
                base_url=config.rerank_base_url,
                api_key=config.rerank_api_key,
                model=config.rerank_model_name,
            ),
            answer_agent=AnswerAgent(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model_name,
            ),
            top_n=args.top_n,
            top_k=args.top_k,
            trace=trace,
        )
    finally:
        close_trace(trace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _generation_result_payload(result) -> dict:
    return {
        "case_name": result.case_name,
        "status": result.status,
        "evidence_paths": [str(path) for path in result.evidence_paths],
        "failure_paths": [str(path) for path in result.failure_paths],
        "insights_path": str(result.insights_path) if result.insights_path else None,
        "playbook_path": str(result.playbook_path) if result.playbook_path else None,
        "error": result.error,
        "case_part_errors": dict(getattr(result, "case_part_errors", ()) or ()),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "parse":
        return _cmd_parse(args)
    if args.command == "generate-insights":
        return _cmd_generate_insights(args)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "index":
        return _cmd_index(args)
    if args.command == "parse-course":
        return _cmd_parse_course(args)
    if args.command == "ingest-course":
        return _cmd_ingest_course(args)
    if args.command == "search":
        return _cmd_search(args)
    if args.command == "answer":
        return _cmd_answer(args)
    parser.error(f"未知命令: {args.command}")
    return 2
