from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_offline_compose_is_image_only_and_uses_uncommon_web_port() -> None:
    compose = read_repo_file("docker-compose.offline.yml")

    assert "build:" not in compose
    assert "image: xhbx-rag-api:latest" in compose
    assert "image: xhbx-rag-web:latest" in compose
    assert '"${WEB_PORT:-18088}:80"' in compose
    assert "./parsed:/app/parsed:ro" in compose
    assert "--workers" not in compose
    for service_name in ("api", "web", "etcd", "minio", "standalone", "cli"):
        assert f"  {service_name}:" in compose


def test_offline_env_has_model_and_dual_collection_settings() -> None:
    env_template = read_repo_file(".env.offline.example")

    for setting in (
        "API_KEY=",
        "BASE_URL=",
        "MODEL_NAME=",
        "EMBEDDING_BASE_URL=",
        "EMBEDDING_MODEL_NAME=",
        "EMBEDDING_API_KEY=",
        "RERANK_BASE_URL=",
        "RERANK_MODEL_NAME=",
        "RERANK_API_KEY=",
        "MILVUS_COLLECTION=xhbx_sales_chunks",
        "MILVUS_COURSE_COLLECTION=xhbx_course_chunks",
        "WEB_PORT=18088",
    ):
        assert setting in env_template


def test_index_script_routes_case_and_course_files(tmp_path: Path) -> None:
    parsed = tmp_path / "parsed"
    (parsed / "case-a").mkdir(parents=True)
    (parsed / "case-b").mkdir()
    (parsed / "chunk").mkdir()
    (parsed / "case-a" / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    (parsed / "case-b" / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    (parsed / "chunk" / "a.chunks.jsonl").write_text("{}\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.log"
    fake_cli = fake_bin / "xhbx-rag"
    fake_cli.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n',
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PARSED_DIR": str(parsed),
        "CALL_LOG": str(call_log),
    }

    result = subprocess.run(
        ["sh", str(ROOT / "scripts/index_parsed_offline.sh"), "all"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    assert "--collection case --mode rebuild" in calls[0]
    assert "--collection case --mode incremental" in calls[1]
    assert "--collection course --mode rebuild" in calls[2]
    assert all("parsed/chunk" not in call for call in calls[:2])


def test_index_script_rejects_unknown_target_without_running_cli(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/index_parsed_offline.sh"), "unknown"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "all|case|course" in result.stderr


def test_deploy_script_validates_before_loading_and_indexes_before_web() -> None:
    script = read_repo_file("scripts/deploy_web_offline.sh")

    main_flow = script[script.index("# 主部署流程") :]
    checksum = main_flow.index("verify_checksum")
    load = main_flow.index('docker load -i "$IMAGE_TAR"')
    index = main_flow.index("index_parsed_offline.sh all")
    web = main_flow.index("up -d --no-build api web")
    verify = main_flow.index("verify_web_offline.sh")
    assert checksum < load < index < web < verify
    assert "uname -m" in script
    assert "package-manifest.txt" in script
    assert "缺少必要环境变量" in script


def test_verify_script_checks_services_collections_and_real_answer() -> None:
    script = read_repo_file("scripts/verify_web_offline.sh")

    for service in ("etcd", "minio", "standalone", "api", "web"):
        assert service in script
    assert "get_collection_stats" in script
    assert "/api/status" in script
    assert "/api/answer" in script
    assert "SMOKE_QUERY" in script
    assert "row_count" in script
