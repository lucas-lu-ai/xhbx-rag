from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .chunk_builder import build_chunks
from .config import RetrievalConfig
from .embedding import EmbeddingClient
from .indexer import index_chunks
from .milvus_store import MilvusLiteStore
from .normalizer import normalize_case
from .parser import ParseFatalError, parse_inputs
from .query_understanding import QueryUnderstandingAgent
from .rerank import RerankClient
from .report import build_parse_report
from .search import search_evidence
from .writer import write_outputs


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

    index_parser = subparsers.add_parser("index", help="写入 chunks.jsonl 到 Milvus Lite")
    index_parser.add_argument("--chunks", required=True, type=Path)

    search_parser = subparsers.add_parser("search", help="检索销售洞察 evidence chunks")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top-n", type=int, default=20)
    search_parser.add_argument("--top-k", type=int, default=5)
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


def _embedding_client(config: RetrievalConfig) -> EmbeddingClient:
    return EmbeddingClient(
        base_url=config.embedding_base_url,
        api_key=config.embedding_api_key,
        model=config.embedding_model_name,
    )


def _milvus_store(config: RetrievalConfig) -> MilvusLiteStore:
    return MilvusLiteStore(
        db_path=config.milvus_lite_path,
        collection_name=config.milvus_collection,
    )


def _cmd_index(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    count = index_chunks(
        args.chunks,
        _embedding_client(config),
        _milvus_store(config),
    )
    print(json.dumps({"indexed": count}, ensure_ascii=False))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env()
    result = search_evidence(
        query=args.query,
        query_agent=QueryUnderstandingAgent(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model_name,
        ),
        embedding_client=_embedding_client(config),
        store=_milvus_store(config),
        reranker=RerankClient(
            base_url=config.rerank_base_url,
            api_key=config.rerank_api_key,
            model=config.rerank_model_name,
        ),
        top_n=args.top_n,
        top_k=args.top_k,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "parse":
        return _cmd_parse(args)
    if args.command == "index":
        return _cmd_index(args)
    if args.command == "search":
        return _cmd_search(args)
    parser.error(f"未知命令: {args.command}")
    return 2
