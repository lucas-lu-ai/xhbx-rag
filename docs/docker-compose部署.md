# xhbx-rag Docker Compose 部署文档

本文档说明如何用 `docker compose` 部署 `xhbx-rag`。部署形态为：

- Web 问答界面常驻运行。
- FastAPI 后端常驻运行。
- Milvus Standalone、etcd、MinIO 常驻运行。
- `generate-insights -> parse -> index` 入库流水线通过一次性 compose 任务执行，不作为常驻服务。

## 1. 文件说明

本次部署相关文件：

- `docker-compose.yml`：服务编排，包含 `web`、`api`、`standalone`、`etcd`、`minio`、`cli`。
- `Dockerfile.api`：后端和 CLI 共用的 Python 镜像。
- `web/Dockerfile`：前端构建和 Nginx 静态服务镜像。
- `web/nginx.conf`：前端静态文件服务和 `/api` 反向代理配置。
- `.env.docker.example`：Docker 部署环境变量模板。
- `.dockerignore`：Docker 构建上下文忽略规则。

其中 `cli` 是一次性任务服务，复用 `api` 镜像，不常驻运行。

## 2. 准备环境变量

复制模板：

```bash
cp .env.docker.example .env
```

填入真实模型、embedding、rerank 凭证：

```env
API_KEY=
BASE_URL=
MODEL_NAME=

EMBEDDING_BASE_URL=https://api.siliconflow.com/v1
EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-8B
EMBEDDING_API_KEY=

RERANK_BASE_URL=https://api.siliconflow.com/v1
RERANK_MODEL_NAME=Qwen/Qwen3-Reranker-8B
RERANK_API_KEY=
```

Milvus 配置保持为 Docker 模式：

```env
MILVUS_MODE=docker
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=
MILVUS_COLLECTION=xhbx_sales_chunks
MILVUS_VECTOR_DIM=
```

说明：

- `.env` 中的 `MILVUS_URI=http://localhost:19530` 方便宿主机直接运行 `uv run xhbx-rag ...` 调试 Docker Milvus。
- 在 `api` 和 `cli` 容器内，`docker-compose.yml` 会覆盖为 `http://standalone:19530`。
- 如果 Docker Milvus 未启用鉴权，`MILVUS_TOKEN` 留空。

## 3. 启动常驻服务

构建并启动：

```bash
docker compose up -d --build
```

查看服务状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f api
docker compose logs -f web
docker compose logs -f standalone
```

默认访问地址：

- Web 页面：`http://localhost:8080`
- API 状态：`http://127.0.0.1:8000/api/status`
- Milvus：`http://127.0.0.1:19530`
- Milvus health：`http://127.0.0.1:9091/healthz`
- MinIO Console：`http://127.0.0.1:9001`

## 4. 入库流水线

原始案例素材放在项目 `data/` 目录下。容器工作目录是 `/app`，命令里的路径继续使用项目相对路径。

一键执行生成、解析、入库：

```bash
docker compose run --rm cli xhbx-rag ingest --case-dir "data/绩优案例/某案例"
```

只重新入库已经解析好的 chunks：

```bash
docker compose run --rm cli xhbx-rag index --chunks "parsed/某案例/chunks.jsonl" --mode incremental
```

重建 collection：

```bash
docker compose run --rm cli xhbx-rag index --chunks "parsed/某案例/chunks.jsonl" --mode rebuild
```

批量脚本也可以在容器内执行。`docker-compose.yml` 会把宿主机 `./scripts` 只读挂载到 `/app/scripts`：

```bash
docker compose run --rm cli scripts/ingest_cases.sh --list scripts/cases.txt --continue-on-error
```

## 5. 数据持久化

项目目录挂载：

- `./data:/app/data`
- `./.local:/app/.local`
- `./generated:/app/generated`
- `./parsed:/app/parsed`
- `./scripts:/app/scripts:ro`

这些目录的用途：

- `data`：原始案例素材和引用文件。
- `.local`：Web 批量执行 SQLite 等本地状态。
- `generated`：`generate-insights` 产物。
- `parsed`：`parse` 产物和 `chunks.jsonl`。
- `scripts`：宿主机上的批量脚本，容器内只读使用。

Milvus 内部数据使用 Docker named volumes：

- `xhbx-rag_milvus_etcd`
- `xhbx-rag_milvus_minio`
- `xhbx-rag_milvus_data`

停止服务但保留数据：

```bash
docker compose down
```

删除 Milvus 数据卷：

```bash
docker compose down -v
```

只有确认要清空 Docker Milvus 数据时才执行 `down -v`。

## 6. 端口调整

默认端口可在 `.env` 中覆盖：

```env
WEB_PORT=8080
API_PORT=8000
MILVUS_PORT=19530
MILVUS_HEALTH_PORT=9091
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001
```

如果本机端口已被占用，可以临时指定：

```bash
WEB_PORT=18080 API_PORT=18000 MILVUS_PORT=19531 docker compose up -d --build
```

## 7. 部署约束

- Web 后端必须保持单 uvicorn worker。批量执行依赖进程内后台线程，不能用多 worker 部署。
- 已有 Milvus Lite 文件不能直接作为 Docker Milvus 数据目录使用。需要用 `parsed/*/chunks.jsonl` 重新执行 `xhbx-rag index`。
- Docker 容器内没有 macOS Finder，“在 Finder 中显示文件”按钮不适合作为服务器部署能力；页面仍会展示引用路径、定位和原文摘录。
- API、Milvus、MinIO 调试端口默认绑定在宿主机回环地址，避免默认暴露到公网。Web 端口默认对宿主机开放。

## 8. 快速验证

检查 compose 配置：

```bash
docker compose config --quiet
```

构建镜像：

```bash
docker compose build api web
```

验证 CLI 入口：

```bash
docker compose run --rm --no-deps cli xhbx-rag --help
```

启动完整服务并等待健康检查：

```bash
docker compose up -d --wait
```

检查 API：

```bash
curl -fsS http://127.0.0.1:8000/api/status
```

检查 Web：

```bash
curl -fsSI http://127.0.0.1:8080/
```
