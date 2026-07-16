# xhbx-rag Web 问答界面离线部署文档

本文档用于把当前仓库的完整 Web 问答界面交付到无法访问外网的 Linux 私有环境。目标环境已经提供 Chat、Embedding、Rerank 三类内网模型接口，因此离线包不包含模型权重和推理服务。

## 1. 部署结论

联网打包机执行一条命令，生成与目标服务器架构一致的完整离线包：

```bash
sh scripts/package_web_offline.sh amd
```

或：

```bash
sh scripts/package_web_offline.sh arm
```

离线服务器解压、配置三个内网模型接口后执行：

```bash
sh deploy_web_offline.sh
```

脚本会自动完成镜像导入、Milvus 基础服务启动、案例库入库、课程库入库、API/Web 启动和真实问答验证。默认访问地址：

```text
http://服务器IP:18088
```

默认端口可在 `.env` 中通过 `WEB_PORT` 修改。

## 2. 服务架构

离线 Compose 常驻以下服务：

| 服务 | 镜像 | 作用 | 默认宿主机端口 |
| --- | --- | --- | --- |
| `web` | `xhbx-rag-web:latest` | Nginx 托管 React 页面并反代 `/api/` | `18088`，内网用户访问 |
| `api` | `xhbx-rag-api:latest` | FastAPI/Uvicorn 问答服务 | `127.0.0.1:8000` |
| `standalone` | `milvusdb/milvus:v2.6.19` | Milvus Standalone 向量库 | `127.0.0.1:19530/9091` |
| `etcd` | `quay.io/coreos/etcd:v3.5.25` | Milvus 元数据 | 仅 Compose 内网 |
| `minio` | `minio/minio:RELEASE.2024-12-18T13-15-44Z` | Milvus 对象存储 | `127.0.0.1:9000/9001` |

`cli` 使用 API 镜像执行一次性入库和验证任务，不是常驻服务。

Web 后端保持单个 Uvicorn worker。当前 Web 文档入库 Runner、批量任务 Runner 和同机文件锁都按单 API 进程设计，不要自行增加 `--workers` 或启动多个 API 副本并发写同一 collection。

## 3. 知识库数据与检索行为

离线包包含两类 chunk 数据：

| 包内数据 | 当前基线 | 写入目标 |
| --- | ---: | --- |
| `parsed/*/chunks.jsonl` | 101 个文件，约 2,631 条 | `MILVUS_COLLECTION=xhbx_sales_chunks`，案例库 |
| `parsed/chunk/*.chunks.jsonl` | 977 个文件，约 16,299 条 | `MILVUS_COURSE_COLLECTION=xhbx_course_chunks`，课程库 |

每次正式打包都会重新统计，准确数量以包内 `package-manifest.txt` 为准。

用户提问时，查询理解模型会自动输出 `collection_targets`：

- 绩优案例、实战经验类问题通常只查案例库。
- 课程教材、标准流程、培训内容通常只查课程库。
- 混合问题或无法确定时同时查询两个库。
- 同时查询时，两库候选会合并后统一执行关键词召回、融合排序和 Rerank，最终只生成一个答案。

因此不是每个问题都固定查询两个库，但两个 collection 都必须在部署时初始化。

## 4. 不包含的内容

主离线包不携带 data/ 目录中的现有原始资料，也不包含：

- Chat、Embedding、Rerank 模型权重或推理服务。
- 真实模型 Key。
- `.local/` 中的本机任务状态。
- `generated/` 生成产物。
- `parsed/` 中除入库 chunk JSONL 以外的辅助文件。

不携带 `data/` 不影响基于 chunk 的问答、引用正文和引用元数据，但会产生以下限制：

- 不能在服务器上打开原始来源文件。
- 不能仅依靠离线包重新解析原始 Word、PDF、PPT 等文件。
- 历史 citation 中的绝对路径可能来自原加工环境，只是元数据，不代表服务器存在该文件。

部署脚本会创建一个空 `data/` 运行目录，供后续 Web 上传和新数据处理使用。

## 5. 环境要求

### 5.1 联网打包机

联网打包机需要：

- 当前完整 Git 仓库。
- 可访问 Docker Hub、Quay、GitHub Container Registry、Python 和 npm 依赖源。
- Docker Engine 或 Docker Desktop。
- Docker Buildx。
- Docker Compose v2 插件或兼容的 `docker-compose`。
- `sha256sum` 或 `shasum`。
- 建议至少预留 30GB 可用磁盘，用于构建缓存、镜像和离线包。

打包机可以是 macOS 或 Linux。脚本使用 POSIX shell，数据扫描不依赖 GNU `find -maxdepth`。

### 5.2 离线服务器

离线服务器需要：

- Linux `x86_64` 或 `ARM64`。
- 已安装 Docker Engine。
- Docker Compose v2 插件或兼容的 `docker-compose`。
- `curl`。
- `sha256sum` 或 `shasum`。
- 能从 Docker 容器网络访问三个内网模型接口。
- 推荐至少 8 vCPU、32GB 内存、100GB 可用 SSD；模型服务不在本机时不需要 GPU。

注意：如果模型服务运行在同一台物理服务器上，`.env` 中不要直接使用 `127.0.0.1`。容器内的 `127.0.0.1` 指向容器自身，应使用宿主机内网 IP 或客户环境提供的容器可达地址。

## 6. 确认目标服务器架构

在离线服务器执行：

```bash
uname -m
```

对应关系：

| 输出 | 打包参数 | Docker 平台 |
| --- | --- | --- |
| `x86_64`、`amd64` | `amd` | `linux/amd64` |
| `aarch64`、`arm64` | `arm` | `linux/arm64` |

如果暂时不知道架构，可以分别生成两个包，但服务器只能导入与自身架构匹配的那个包。

## 7. 在联网环境打包

进入仓库根目录：

```bash
cd /path/to/xhbx-rag
```

AMD64：

```bash
sh scripts/package_web_offline.sh amd
```

输出：

```text
dist/xhbx-rag-web-offline-amd64.tar.gz
```

ARM64：

```bash
sh scripts/package_web_offline.sh arm
```

输出：

```text
dist/xhbx-rag-web-offline-arm64.tar.gz
```

打包脚本会：

1. 校验 `amd|arm` 参数。
2. 检查两类 chunk 数据均存在。
3. 按目标平台构建 API 和 Web 镜像。
4. 按目标平台拉取 etcd、MinIO、Milvus 固定版本镜像。
5. 逐个检查镜像 OS/Architecture。
6. 导出 `images.tar` 并生成 `images.sha256`。
7. 复制两类 chunk JSONL 并生成逐文件 SHA-256 清单。
8. 复制离线 Compose、环境模板、部署脚本、验证脚本和本文档。
9. 生成最终压缩包。

打包期间需要外网；生成后的离线包运行时不需要外网。

## 8. 离线包结构

解压后的目录类似：

```text
xhbx-rag-web-offline-amd64/
├── images.tar
├── images.sha256
├── package-manifest.txt
├── docker-compose.offline.yml
├── .env.offline.example
├── parsed/
│   ├── <案例目录>/chunks.jsonl
│   └── chunk/*.chunks.jsonl
├── deploy_web_offline.sh
├── verify_web_offline.sh
├── scripts/
│   └── index_parsed_offline.sh
└── README.offline.md
```

`package-manifest.txt` 包含：

- 目标 Docker 平台。
- 镜像列表。
- 案例文件数与 chunk 数。
- 课程文件数与 chunk 数。
- 每个数据文件的 SHA-256。

## 9. 传输和解压

通过客户允许的离线介质、堡垒机文件通道或内网文件服务，把对应压缩包传到服务器。

解压：

```bash
tar -xzf xhbx-rag-web-offline-amd64.tar.gz
cd xhbx-rag-web-offline-amd64
```

ARM64 时替换目录和文件名中的 `amd64`。

可在部署前人工复核镜像校验值：

```bash
sha256sum images.tar
cat images.sha256
```

`deploy_web_offline.sh` 也会自动校验，不一致时不会导入镜像。

## 10. 配置内网模型

复制模板：

```bash
cp .env.offline.example .env
chmod 600 .env
```

编辑 `.env`：

```env
API_KEY=not-required
BASE_URL=http://10.10.10.20:8000/v1
MODEL_NAME=private-chat-model

EMBEDDING_BASE_URL=http://10.10.10.21:8000/v1
EMBEDDING_MODEL_NAME=private-embedding-model
EMBEDDING_API_KEY=not-required

RERANK_BASE_URL=http://10.10.10.22:8000/v1
RERANK_MODEL_NAME=private-rerank-model
RERANK_API_KEY=not-required

MILVUS_MODE=docker
MILVUS_COLLECTION=xhbx_sales_chunks
MILVUS_COURSE_COLLECTION=xhbx_course_chunks
MILVUS_VECTOR_DIM=

WEB_PORT=18088
```

地址口径：

- 程序会在 `BASE_URL` 后拼接 `/chat/completions`。
- 程序会在 `EMBEDDING_BASE_URL` 后拼接 `/embeddings`。
- 程序会在 `RERANK_BASE_URL` 后拼接 `/rerank`。
- 不要把最终接口路径重复写入根地址，否则会出现重复路径。
- 如果模型接口需要鉴权，填写真实内部凭证；如果不鉴权，三个 Key 使用 `not-required` 占位，因为当前程序要求这些字段非空。
- `MILVUS_VECTOR_DIM` 留空时由首次 Embedding 结果决定；手工填写时必须与模型实际向量维度一致。

不要把带真实 Key 的 `.env` 重新打进离线包，也不要提交到 Git。

## 11. 首次一键部署

确认当前目录为解压后的包目录，然后执行：

```bash
sh deploy_web_offline.sh
```

执行顺序：

```text
校验 images.tar
→ 校验服务器架构
→ 校验 .env 必填字段
→ docker load
→ 启动 etcd、MinIO、Milvus
→ 等待基础服务健康
→ 重建并导入案例库
→ 重建并导入课程库
→ 启动 API、Web
→ 检查双库和服务状态
→ 执行真实问答冒烟验证
```

案例库和课程库都成功后才会启动 API/Web。任何一步失败，脚本返回非零，不会输出“部署完成”。

首次入库需要对约 18,930 条 chunk 调用内网 Embedding 服务。实际耗时取决于模型吞吐、网络延迟、单次批量能力和服务器磁盘性能。

部署完成后访问：

```text
http://服务器IP:18088
```

## 12. 部署后验证

部署脚本已自动执行验证。也可以单独重跑：

```bash
sh verify_web_offline.sh
```

自定义冒烟问题：

```bash
SMOKE_QUERY="如何处理客户认为保险太贵的异议？" sh verify_web_offline.sh
```

验证内容：

1. etcd、MinIO、Milvus、API、Web 容器运行且健康。
2. `xhbx_sales_chunks` 存在且 `row_count > 0`。
3. `xhbx_course_chunks` 存在且 `row_count > 0`。
4. `/api/status` 返回 `ok=true`。
5. Web 首页可访问。
6. `/api/answer` 完成一次真实问题，贯通 Chat、Embedding、Milvus、Rerank 和答案生成。

单独检查服务：

```bash
docker compose -f docker-compose.offline.yml ps
curl -fsS http://127.0.0.1:8000/api/status
curl -fsSI http://127.0.0.1:18088/
```

## 13. 日常启停

启动或恢复服务，不重新入库：

```bash
docker compose -f docker-compose.offline.yml up -d --no-build
```

停止容器但保留 Milvus 数据卷：

```bash
docker compose -f docker-compose.offline.yml down
```

重启 API 和 Web：

```bash
docker compose -f docker-compose.offline.yml restart api web
```

普通 `up`、`restart` 不会调用 Embedding，也不会重建 collection。只有重新执行 `deploy_web_offline.sh` 或显式运行入库脚本才会重建。

## 14. 查看日志

API 和 Web：

```bash
docker compose -f docker-compose.offline.yml logs -f --tail=200 api web
```

Milvus：

```bash
docker compose -f docker-compose.offline.yml logs -f --tail=200 standalone
```

etcd 和 MinIO：

```bash
docker compose -f docker-compose.offline.yml logs -f --tail=200 etcd minio
```

最近 200 行但不持续跟随：

```bash
docker compose -f docker-compose.offline.yml logs --tail=200 api
```

## 15. 更新应用但不重建知识库

在联网环境使用相同目标平台重新构建 API/Web 镜像并导出，或者从新的完整离线包中取得 `images.tar`。传入服务器后：

```bash
docker load -i images.tar
docker compose -f docker-compose.offline.yml up -d \
  --no-deps --no-build --force-recreate api web
sh verify_web_offline.sh
```

这条路径不会重启 Milvus、etcd、MinIO，也不会重新入库。

如果新版本修改了 Milvus schema、Embedding 模型或向量维度，不能只更新应用；必须按完整部署流程重建 collection。

## 16. 更新 chunks 并重建知识库

推荐重新生成完整离线包，将新包传入服务器，更新 `.env` 后执行：

```bash
sh deploy_web_offline.sh
```

脚本会分别重建案例库和课程库，确保删除新数据集中已经不存在的旧 chunk。

只重建案例库：

```bash
docker compose -f docker-compose.offline.yml run --rm --no-deps cli \
  sh /app/scripts/index_parsed_offline.sh case
```

只重建课程库：

```bash
docker compose -f docker-compose.offline.yml run --rm --no-deps cli \
  sh /app/scripts/index_parsed_offline.sh course
```

手工入库完成后执行：

```bash
sh verify_web_offline.sh
```

不要把 `parsed/chunk/*.chunks.jsonl` 当成案例数据写进 `xhbx_sales_chunks`；两个库的重建互相独立。

## 17. 备份

需要备份：

- Docker named volumes：`milvus_data`、`milvus_etcd`、`milvus_minio`。
- `.local/`：Web 上传任务、批量任务和 bad case 状态。
- `.env`：模型和端口配置，按敏感文件管理。
- 当前离线包或至少 `parsed/` 与 `package-manifest.txt`。

查看实际卷名：

```bash
docker volume ls --filter label=com.docker.compose.project=xhbx-rag-offline
```

备份前停止服务，避免跨卷时间点不一致：

```bash
docker compose -f docker-compose.offline.yml down
mkdir -p backup
```

使用已经离线导入的 API 镜像和 Python 标准库备份卷，不需要额外拉取工具镜像。以 Milvus 主数据卷为例：

```bash
docker run --rm \
  -v xhbx-rag-offline_milvus_data:/source:ro \
  -v "$PWD/backup:/backup" \
  xhbx-rag-api:latest \
  python -c 'import tarfile; t=tarfile.open("/backup/milvus_data.tar.gz","w:gz"); t.add("/source",arcname="."); t.close()'
```

将卷名和输出文件名替换为以下两组，再分别执行：

```text
xhbx-rag-offline_milvus_etcd  → milvus_etcd.tar.gz
xhbx-rag-offline_milvus_minio → milvus_minio.tar.gz
```

备份目录状态和配置：

```bash
tar -czf backup/local.tar.gz .local
cp .env backup/env.backup
chmod 600 backup/env.backup
```

备份完成后重新启动：

```bash
docker compose -f docker-compose.offline.yml up -d --no-build
sh verify_web_offline.sh
```

恢复时必须保证三个 Milvus 相关卷来自同一次停机备份。先停止服务、创建空卷，再用同一个 `xhbx-rag-api:latest` 镜像和 Python `tarfile` 解压到对应卷。恢复属于高风险操作，应先在隔离服务器演练。

## 18. 常见故障

### 18.1 离线包平台不匹配

错误示例：

```text
离线包平台不匹配: package=linux/amd64 server=linux/arm64
```

处理：回到联网打包机，使用正确的 `amd` 或 `arm` 参数重新打包。不要使用模拟方式在错误架构服务器上运行生产服务。

### 18.2 镜像校验失败

处理：重新传输整个压缩包，重新解压，不要只替换 `images.sha256`。

### 18.3 `.env` 缺少字段

部署脚本只打印缺少的变量名，不会打印 Key。补全后重新执行：

```bash
sh deploy_web_offline.sh
```

### 18.4 容器无法访问模型服务

先从 CLI 容器测试地址可达性。下面示例只检查 HTTP 连通性，不验证业务响应：

```bash
docker compose -f docker-compose.offline.yml run --rm --no-deps cli \
  curl -v http://10.10.10.20:8000/v1/models
```

如果宿主机可访问但容器不可访问，检查防火墙、模型服务监听地址、Docker 网段路由和客户网络 ACL。

### 18.5 入库阶段失败

查看终端中最后一个失败的相对 JSONL 路径和模型错误。修复模型配置或数据后，重新执行完整部署脚本。脚本会重新 `rebuild`，不会把半成品当作成功结果。

### 18.6 API 健康但真实问答失败

`/api/status` 主要检查配置是否齐全，不代表三个模型接口一定兼容。运行：

```bash
sh verify_web_offline.sh
docker compose -f docker-compose.offline.yml logs --tail=200 api
```

重点检查 Chat 是否支持 JSON object 输出和流式回答、Embedding 维度是否稳定、Rerank 响应是否包含结果索引和相关性分数。

### 18.7 18088 端口被占用

检查：

```bash
ss -ltnp | grep ':18088'
```

修改 `.env`：

```env
WEB_PORT=28088
```

重建 Web 容器：

```bash
docker compose -f docker-compose.offline.yml up -d --no-build --force-recreate web
```

### 18.8 Milvus 不健康

```bash
docker compose -f docker-compose.offline.yml ps
docker compose -f docker-compose.offline.yml logs --tail=200 standalone etcd minio
df -h
```

优先检查磁盘空间、三个数据卷、etcd/MinIO 健康状态和 Docker 守护进程日志。

## 19. 安全要求

- `.env` 权限设置为 `600`，不得提交到代码库或放回离线交付包。
- Web 当前没有内建企业统一认证，只允许可信内网访问，生产建议放在客户现有网关、VPN 或统一认证代理之后。
- API、Milvus、MinIO 默认只绑定 `127.0.0.1`，不要改成公网监听。
- MinIO 内部凭据沿用当前仓库默认配置，仅依靠 Docker 私有网络与回环端口隔离。如果客户安全规范要求服务间强鉴权，应单独进行配置加固和验证。
- 离线包中的 chunk 仍属于业务知识数据，应按客户数据分级要求存储、传输和销毁。
- 部署日志不得打印或上传真实模型 Key。

## 20. 破坏性命令警告

普通停止应使用：

```bash
docker compose -f docker-compose.offline.yml down
```

下面的 docker compose down -v 会删除 Milvus、etcd、MinIO named volumes：

```bash
docker compose -f docker-compose.offline.yml down -v
```

只有确认已经完成可恢复备份并且确实需要清空整个向量库时才能执行。日常更新、重启、排障都不需要 `-v`。

## 21. 交付验收清单

- [ ] 服务器架构与离线包平台一致。
- [ ] `images.tar` SHA-256 校验通过。
- [ ] 三类内网模型地址可从容器访问。
- [ ] `.env` 九个模型必填字段非空。
- [ ] 五个常驻服务健康。
- [ ] 案例库 `xhbx_sales_chunks` 非空。
- [ ] 课程库 `xhbx_course_chunks` 非空。
- [ ] `sh verify_web_offline.sh` 全部通过。
- [ ] 用户能通过 `http://服务器IP:18088` 打开页面并完成问答。
- [ ] 已确认不携带原始 `data/` 所带来的来源文件限制。
- [ ] 已建立 `.env`、`.local` 和三个 Milvus 相关卷的备份方案。
