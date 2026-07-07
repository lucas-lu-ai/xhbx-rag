from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_docker_deployment_files_exist() -> None:
    expected_files = [
        "Dockerfile.api",
        "web/Dockerfile",
        "web/nginx.conf",
        "docker-compose.yml",
        "docker-compose.mcp.yml",
        ".dockerignore",
        ".env.docker.example",
        ".env.mcp.example",
        "docs/docker-compose部署.md",
    ]

    missing = [path for path in expected_files if not (ROOT / path).is_file()]

    assert missing == []


def test_compose_defines_runtime_services_and_milvus_dependencies() -> None:
    compose = read_repo_file("docker-compose.yml")

    for service_name in ("api", "web", "etcd", "minio", "standalone", "cli"):
        assert f"  {service_name}:" in compose

    assert "dockerfile: Dockerfile.api" in compose
    assert "image: xhbx-rag-api:latest" in compose
    assert "dockerfile: web/Dockerfile" in compose
    assert "milvusdb/milvus:v2.6.19" in compose
    assert "quay.io/coreos/etcd:v3.5.25" in compose
    assert "minio/minio:RELEASE.2024-12-18T13-15-44Z" in compose
    assert "condition: service_healthy" in compose
    assert "http://standalone:19530" in compose


def test_compose_mounts_project_data_and_uses_single_api_worker() -> None:
    compose = read_repo_file("docker-compose.yml")

    for mount in (
        "./data:/app/data",
        "./.local:/app/.local",
        "./generated:/app/generated",
        "./parsed:/app/parsed",
        "./scripts:/app/scripts:ro",
    ):
        assert mount in compose

    assert "uvicorn" in compose
    assert "xhbx_rag.web.app:app" in compose
    assert "--host" in compose
    assert "0.0.0.0" in compose
    assert "--port" in compose
    assert "8000" in compose
    assert "--workers" not in compose


def test_nginx_serves_frontend_and_proxies_api_streams() -> None:
    nginx = read_repo_file("web/nginx.conf")

    assert "root /usr/share/nginx/html;" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000;" in nginx
    assert "proxy_buffering off;" in nginx
    assert "proxy_read_timeout 3600s;" in nginx


def test_api_dockerfile_installs_uv_project_without_dev_dependencies() -> None:
    dockerfile = read_repo_file("Dockerfile.api")

    assert "FROM python:3.12-slim" in dockerfile
    assert "COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/" in dockerfile
    assert "UV_NO_DEV=1" in dockerfile
    assert "uv sync --locked" in dockerfile
    assert 'CMD ["uvicorn", "xhbx_rag.web.app:app"' in dockerfile
    assert "COPY scripts" not in dockerfile
    assert "chmod +x scripts" not in dockerfile


def test_docker_docs_cover_startup_and_one_off_ingestion_tasks() -> None:
    docs = read_repo_file("docs/docker-compose部署.md")

    assert "docker compose up -d --build" in docs
    assert "docker compose run --rm cli xhbx-rag ingest" in docs
    assert "docker compose run --rm cli xhbx-rag index" in docs
    assert "MILVUS_URI=http://localhost:19530" in docs
    assert "http://standalone:19530" in docs


def test_mcp_compose_defines_only_mcp_and_milvus_stack() -> None:
    compose = read_repo_file("docker-compose.mcp.yml")

    for service_name in ("mcp", "etcd", "minio", "standalone"):
        assert f"  {service_name}:" in compose

    assert "  api:" not in compose
    assert "  web:" not in compose
    assert "  cli:" not in compose
    assert "dockerfile: Dockerfile.api" in compose
    assert "xhbx-rag-mcp" in compose
    assert "streamable-http" in compose
    assert "--path" in compose
    assert "/mcp" in compose
    assert "http://standalone:19530" in compose
    assert "./parsed:/app/parsed" in compose
    assert "./scripts:/app/scripts:ro" in compose
    assert '"${MCP_BIND:-127.0.0.1}:${MCP_PORT:-9331}:9331"' in compose


def test_mcp_env_template_documents_server_binding_and_collections() -> None:
    env_template = read_repo_file(".env.mcp.example")

    assert "MCP_BIND=127.0.0.1" in env_template
    assert "MCP_PORT=9331" in env_template
    assert "MILVUS_MODE=docker" in env_template
    assert "MILVUS_URI=http://localhost:19530" in env_template
    assert "MILVUS_COLLECTION=xhbx_sales_chunks" in env_template
    assert "MILVUS_COURSE_COLLECTION=xhbx_course_chunks" in env_template


def test_index_parsed_script_indexes_every_chunks_jsonl_under_parsed() -> None:
    script = read_repo_file("scripts/index_parsed.sh")

    assert 'PARSED_DIR="${PARSED_DIR:-parsed}"' in script
    assert 'RESET_COLLECTION="${RESET_COLLECTION:-false}"' in script
    assert 'find "$PARSED_DIR" -type f -name "chunks.jsonl"' in script
    assert 'current_mode="rebuild"' in script
    assert 'xhbx-rag index --chunks "$chunks_file" --mode "$current_mode"' in script
