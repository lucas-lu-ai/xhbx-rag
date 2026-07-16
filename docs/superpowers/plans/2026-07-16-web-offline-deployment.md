# Web Offline Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为完整 Web 问答界面增加按 AMD64/ARM64 打包的离线交付包、一键双库初始化部署、部署后验证和可直接交付的中文部署文档。

**Architecture:** 联网打包机用 Buildx 构建 API/Web 镜像并导出固定版本的 Milvus 依赖镜像，同时只复制案例与课程 chunk JSONL。离线服务器通过独立 Compose 启动基础设施，CLI 一次性任务分别重建案例库和课程库，成功后才启动 API/Web 并执行真实问答冒烟验证。

**Tech Stack:** POSIX shell、Docker Buildx、Docker Compose、Python 3.12/pytest、FastAPI、React/Nginx、Milvus Standalone、etcd、MinIO。

## Global Constraints

- 目标环境不能访问外网，但能访问 Chat、Embedding、Rerank 三类内网模型接口。
- `amd` 映射 `linux/amd64`，`arm` 映射 `linux/arm64`。
- 默认 Web 端口为 `18088`，可通过 `WEB_PORT` 覆盖。
- `parsed/*/chunks.jsonl` 只写案例库；`parsed/chunk/*.chunks.jsonl` 只写课程库。
- 离线包不包含现有 `data/`、真实 Key、`.local/`、`generated/` 或非 chunk parsed 产物。
- 普通 API/Web 重启不触发重新入库。
- 离线 Compose 不得包含 `build:`，后端保持单 Uvicorn worker。
- 不修改或删除工作区现有未跟踪的 `outputs/`。

---

### Task 1: 离线 Compose、环境模板与双库入库脚本

**Files:**
- Create: `docker-compose.offline.yml`
- Create: `.env.offline.example`
- Create: `scripts/index_parsed_offline.sh`
- Create: `tests/test_web_offline_deployment.py`

**Interfaces:**
- Consumes: 当前 `xhbx-rag index --chunks PATH --collection case|course --mode rebuild|incremental` CLI。
- Produces: `docker-compose.offline.yml` 中 `web/api/standalone/etcd/minio/cli` 服务；`sh scripts/index_parsed_offline.sh all|case|course`。

- [ ] **Step 1: 写离线 Compose、环境模板和双库路由的失败测试**

```python
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


def test_offline_env_has_model_and_dual_collection_settings() -> None:
    env = read_repo_file(".env.offline.example")
    for key in (
        "API_KEY=", "BASE_URL=", "MODEL_NAME=",
        "EMBEDDING_BASE_URL=", "EMBEDDING_MODEL_NAME=", "EMBEDDING_API_KEY=",
        "RERANK_BASE_URL=", "RERANK_MODEL_NAME=", "RERANK_API_KEY=",
        "MILVUS_COLLECTION=xhbx_sales_chunks",
        "MILVUS_COURSE_COLLECTION=xhbx_course_chunks",
        "WEB_PORT=18088",
    ):
        assert key in env


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
    fake_cli.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n', encoding="utf-8")
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
    assert "--collection case --mode rebuild" in calls[0]
    assert "--collection case --mode incremental" in calls[1]
    assert "--collection course --mode rebuild" in calls[2]
```

- [ ] **Step 2: 运行测试并确认因文件缺失而失败**

Run: `uv run pytest tests/test_web_offline_deployment.py -q`

Expected: FAIL，错误指出 `docker-compose.offline.yml`、`.env.offline.example` 或 `scripts/index_parsed_offline.sh` 不存在。

- [ ] **Step 3: 实现离线 Compose 与环境模板**

`docker-compose.offline.yml` 必须复用当前线上 Compose 的健康检查、挂载和固定依赖镜像，但删除全部 `build:`，显式声明两个应用镜像，使用 `${ENV_FILE:-.env}`，并设置：

```yaml
services:
  api:
    image: xhbx-rag-api:latest
    env_file:
      - ${ENV_FILE:-.env}
    environment:
      MILVUS_MODE: docker
      MILVUS_URI: http://standalone:19530
    ports:
      - "127.0.0.1:${API_PORT:-8000}:8000"
  cli:
    image: xhbx-rag-api:latest
    profiles: [tools]
  web:
    image: xhbx-rag-web:latest
    ports:
      - "${WEB_PORT:-18088}:80"
```

`.env.offline.example` 必须列出九个必填模型字段、双 collection、Docker Milvus、默认 `WEB_PORT=18088` 和现有 Web 上传/检索限制，Key 留空，不写真实凭证。

- [ ] **Step 4: 实现双库入库脚本**

```sh
#!/bin/sh
set -eu

PARSED_DIR="${PARSED_DIR:-parsed}"
COURSE_DIR="${COURSE_DIR:-$PARSED_DIR/chunk}"
TARGET="${1:-all}"

case "$TARGET" in
  all|case|course) ;;
  *) echo "用法: $0 [all|case|course]" >&2; exit 2 ;;
esac

index_list() {
  list_file="$1"
  collection="$2"
  label="$3"
  if [ ! -s "$list_file" ]; then
    echo "未找到${label} chunk 文件" >&2
    exit 1
  fi
  count=0
  while IFS= read -r chunks_file; do
    count=$((count + 1))
    mode="incremental"
    if [ "$count" -eq 1 ]; then mode="rebuild"; fi
    xhbx-rag index --chunks "$chunks_file" --collection "$collection" --mode "$mode"
  done < "$list_file"
  echo "${label}入库完成：${count} 个文件"
}
```

案例列表使用 POSIX shell glob `"$PARSED_DIR"/*/chunks.jsonl`；课程列表使用 `"$COURSE_DIR"/*.chunks.jsonl`。每次展开后先用 `[ -f "$chunks_file" ]` 过滤未匹配的字面量，再写入临时列表。这样既严格限制目录层级，又兼容 macOS/BSD `find`；两个列表排序后分别传给 `index_list`。

- [ ] **Step 5: 运行任务测试与 Compose 解析**

Run:

```bash
uv run pytest tests/test_web_offline_deployment.py -q
ENV_FILE=.env.offline.example docker compose -f docker-compose.offline.yml config --quiet
```

Expected: 测试 PASS，Compose 命令 exit 0。

- [ ] **Step 6: 提交任务**

```bash
git add docker-compose.offline.yml .env.offline.example scripts/index_parsed_offline.sh tests/test_web_offline_deployment.py
git commit -m "feat: add offline web runtime stack"
```

### Task 2: 离线服务器部署与验证脚本

**Files:**
- Create: `scripts/deploy_web_offline.sh`
- Create: `scripts/verify_web_offline.sh`
- Modify: `tests/test_web_offline_deployment.py`

**Interfaces:**
- Consumes: `images.tar`、`images.sha256`、`package-manifest.txt`、`.env`、`docker-compose.offline.yml`。
- Produces: `sh deploy_web_offline.sh` 与 `sh verify_web_offline.sh`；成功部署后 Web 地址默认 `http://SERVER:18088/`。

- [ ] **Step 1: 添加部署顺序、完整性校验和真实问答验证的失败测试**

```python
def test_deploy_script_validates_before_loading_and_indexes_before_web() -> None:
    script = read_repo_file("scripts/deploy_web_offline.sh")
    checksum = script.index("verify_checksum")
    load = script.index('docker load -i "$IMAGE_TAR"')
    index = script.index("index_parsed_offline.sh all")
    web = script.index("up -d --no-build api web")
    verify = script.index("verify_web_offline.sh")
    assert checksum < load < index < web < verify
    assert "uname -m" in script
    assert "package-manifest.txt" in script


def test_verify_script_checks_services_collections_and_real_answer() -> None:
    script = read_repo_file("scripts/verify_web_offline.sh")
    for service in ("etcd", "minio", "standalone", "api", "web"):
        assert service in script
    assert "get_collection_stats" in script
    assert "/api/status" in script
    assert "/api/answer" in script
    assert "SMOKE_QUERY" in script
```

- [ ] **Step 2: 运行新增测试并确认失败**

Run: `uv run pytest tests/test_web_offline_deployment.py -q`

Expected: FAIL，错误指出两个脚本不存在。

- [ ] **Step 3: 实现部署脚本**

脚本必须提供 `compose()`、`sha256_file()`、`manifest_value()`、`validate_platform()`、`validate_env()`、`wait_for_service()` 函数。主流程严格为：

```sh
verify_checksum
validate_platform
validate_env
ENV_FILE=.env compose -f "$COMPOSE_FILE" config --quiet
docker load -i "$IMAGE_TAR"
mkdir -p data .local
ENV_FILE=.env compose -f "$COMPOSE_FILE" up -d --no-build etcd minio standalone
wait_for_service etcd
wait_for_service minio
wait_for_service standalone
ENV_FILE=.env compose -f "$COMPOSE_FILE" run --rm --no-deps cli \
  sh /app/scripts/index_parsed_offline.sh all
ENV_FILE=.env compose -f "$COMPOSE_FILE" up -d --no-build api web
wait_for_service api
wait_for_service web
sh "$VERIFY_SCRIPT"
```

`.env` 不存在时从模板复制并退出；环境校验只报告变量名。失败 trap 输出 `docker compose ... ps` 及对应日志命令，不执行 `down -v`。

- [ ] **Step 4: 实现验证脚本**

验证脚本使用 `docker inspect` 检查五个服务的运行/健康状态；用一次性 CLI Python 读取两个 collection 的 `get_collection_stats()` 并要求 `row_count > 0`；用宿主机 `curl` 检查 API status 和 Web 首页；最后在 API 容器内用 `urllib.request` POST `/api/answer`，问题来自 `SMOKE_QUERY`，默认“保单整理有什么作用？”，要求 HTTP 200 且 `answer` 非空。

- [ ] **Step 5: 运行测试与 Shell 语法检查**

Run:

```bash
uv run pytest tests/test_web_offline_deployment.py -q
sh -n scripts/deploy_web_offline.sh
sh -n scripts/verify_web_offline.sh
```

Expected: 全部 PASS / exit 0。

- [ ] **Step 6: 提交任务**

```bash
git add scripts/deploy_web_offline.sh scripts/verify_web_offline.sh tests/test_web_offline_deployment.py
git commit -m "feat: automate offline web deployment"
```

### Task 3: AMD/ARM 离线打包脚本

**Files:**
- Create: `scripts/package_web_offline.sh`
- Modify: `tests/test_web_offline_deployment.py`

**Interfaces:**
- Consumes: 仓库源码、固定镜像、两类 chunks、Task 1/2 的运行文件和正式部署文档。
- Produces: `dist/xhbx-rag-web-offline-amd64.tar.gz` 或 `dist/xhbx-rag-web-offline-arm64.tar.gz`。

- [ ] **Step 1: 添加参数映射、镜像导出与数据筛选的失败测试**

```python
def test_package_script_maps_platforms_and_exports_required_assets() -> None:
    script = read_repo_file("scripts/package_web_offline.sh")
    assert 'amd) PLATFORM_SUFFIX="amd64"; DOCKER_PLATFORM="linux/amd64"' in script
    assert 'arm) PLATFORM_SUFFIX="arm64"; DOCKER_PLATFORM="linux/arm64"' in script
    assert "docker buildx build" in script
    assert "xhbx-rag-api:latest" in script
    assert "xhbx-rag-web:latest" in script
    assert 'docker pull --platform "$DOCKER_PLATFORM" "$image"' in script
    assert "docker save" in script
    assert "images.sha256" in script
    assert "package-manifest.txt" in script
    assert "parsed/chunk" in script
    assert "README.offline.md" in script
    assert "data/" not in script


def test_package_script_rejects_invalid_platform_without_docker() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/package_web_offline.sh"), "invalid"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "amd|arm" in result.stderr
```

- [ ] **Step 2: 运行新增测试并确认失败**

Run: `uv run pytest tests/test_web_offline_deployment.py -q`

Expected: FAIL，错误指出打包脚本不存在。

- [ ] **Step 3: 实现平台构建与镜像校验**

脚本先解析唯一位置参数，再检查 `docker`、`docker buildx`、Compose 和所需仓库文件。分别构建：

```sh
docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t xhbx-rag-api:latest -f Dockerfile.api .
docker buildx build --platform "$DOCKER_PLATFORM" --load \
  -t xhbx-rag-web:latest -f web/Dockerfile .
```

固定拉取 etcd、MinIO、Milvus；对五个镜像执行 `docker image inspect --platform "$DOCKER_PLATFORM" --format '{{.Os}}/{{.Architecture}}'`，不匹配立即失败。

- [ ] **Step 4: 实现数据清单、镜像导出和压缩包生成**

脚本用两个排序后的临时列表复制案例和课程 JSONL，逐文件累计行数并写 SHA-256。使用 POSIX `while IFS= read -r` 保留中文与空格文件名。复制离线 Compose、环境模板、三个运行脚本和正式部署文档；设置执行权限；执行 `docker save`、生成 `images.sha256`，最后用 `tar -czf` 原子生成目标压缩包。

- [ ] **Step 5: 运行脚本契约测试与非法参数测试**

Run:

```bash
uv run pytest tests/test_web_offline_deployment.py -q
sh -n scripts/package_web_offline.sh
sh scripts/package_web_offline.sh invalid
```

Expected: pytest PASS，`sh -n` exit 0，非法参数命令 exit 2 且未调用 Docker。

- [ ] **Step 6: 提交任务**

```bash
git add scripts/package_web_offline.sh tests/test_web_offline_deployment.py
git commit -m "feat: package offline web deployment"
```

### Task 4: 正式中文部署文档与全量验证

**Files:**
- Create: `docs/Web问答界面离线部署文档.md`
- Modify: `tests/test_web_offline_deployment.py`

**Interfaces:**
- Consumes: 三个脚本命令、离线 Compose 服务名和 `.env.offline.example` 字段。
- Produces: 联网打包、离线首装、验证、运维、更新、备份恢复与排障手册；打包脚本复制为包内 `README.offline.md`。

- [ ] **Step 1: 添加文档关键命令与边界的失败测试**

```python
def test_offline_deployment_doc_covers_delivery_and_operations() -> None:
    docs = read_repo_file("docs/Web问答界面离线部署文档.md")
    for text in (
        "sh scripts/package_web_offline.sh amd",
        "sh scripts/package_web_offline.sh arm",
        "sh deploy_web_offline.sh",
        "sh verify_web_offline.sh",
        "http://服务器IP:18088",
        "xhbx_sales_chunks",
        "xhbx_course_chunks",
        "parsed/chunk/*.chunks.jsonl",
        "docker compose -f docker-compose.offline.yml logs",
        "docker compose down -v",
        "不携带 data/",
    ):
        assert text in docs
```

- [ ] **Step 2: 运行文档测试并确认失败**

Run: `uv run pytest tests/test_web_offline_deployment.py -q`

Expected: FAIL，错误指出正式部署文档不存在。

- [ ] **Step 3: 编写部署文档**

文档依次包含：架构与边界、联网打包机要求、服务器要求、平台识别、AMD/ARM 打包、包结构、传输与校验、三类内网模型配置、首次自动双库入库、验证、日常启停、日志、仅应用更新、chunks 更新、备份恢复、常见故障、安全加固、无 `data/` 的来源文件限制。所有命令必须与脚本/Compose 文件名一致，破坏性 `down -v` 必须单独警告。

- [ ] **Step 4: 运行全量验证**

Run:

```bash
uv run pytest tests/test_web_offline_deployment.py tests/test_docker_deployment.py -q
uv run pytest -q
npm --prefix web test
npm --prefix web run build
ENV_FILE=.env.offline.example docker compose -f docker-compose.offline.yml config --quiet
sh -n scripts/index_parsed_offline.sh
sh -n scripts/deploy_web_offline.sh
sh -n scripts/verify_web_offline.sh
sh -n scripts/package_web_offline.sh
git diff --check
```

Expected: 所有测试与构建 exit 0；Compose 可解析；Shell 语法通过；无 whitespace 错误。完整多 GB 镜像包不作为本机默认验证步骤。

- [ ] **Step 5: 提交任务**

```bash
git add -f docs/Web问答界面离线部署文档.md
git add tests/test_web_offline_deployment.py
git commit -m "docs: add offline web deployment guide"
```

- [ ] **Step 6: 进行最终差异审查**

Run:

```bash
git status --short
git log -5 --oneline
git diff HEAD~4..HEAD --stat
```

Expected: 只剩用户已有的 `outputs/` 未跟踪内容；最近提交对应设计、运行栈、部署自动化、打包工具与部署文档。
