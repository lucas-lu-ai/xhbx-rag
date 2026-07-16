# Web 问答界面离线部署设计

**日期：** 2026-07-16
**状态：** 已完成交互设计确认，待书面规格复核

## 1. 目标

为 `xhbx-rag` 完整 Web 问答界面提供可重复执行的离线打包与私有环境部署能力。联网打包机根据目标服务器架构生成单个压缩包；目标 Linux 服务器不访问外网，只访问已经部署在私有网络中的 Chat、Embedding 和 Rerank 模型接口。

离线包必须携带当前两类 chunk 数据。首次执行部署脚本时，脚本自动启动 Milvus 基础服务、分别重建案例库和课程库、启动 API 与 Web，并完成健康检查。现有 `data/` 原始资料不进入离线包。

## 2. 已确认约束

- 目标环境不能访问外网。
- 私有环境已有 Chat、Embedding、Rerank 三类内网模型服务。
- 打包平台由命令参数选择：`amd` 对应 `linux/amd64`，`arm` 对应 `linux/arm64`。
- 交付物采用单个完整离线压缩包，不拆分镜像包和数据包。
- 仅携带入库必需的 chunk JSONL，不携带现有 `data/` 原始资料。
- `parsed/*/chunks.jsonl` 写入案例库 `MILVUS_COLLECTION`，默认 `xhbx_sales_chunks`。
- `parsed/chunk/*.chunks.jsonl` 写入课程库 `MILVUS_COURSE_COLLECTION`，默认 `xhbx_course_chunks`。
- 首次部署流程自动入库；API 或 Web 的普通重启不重复入库。
- Web 默认端口改为 `18088`，并允许通过 `.env` 中的 `WEB_PORT` 覆盖。
- API、Milvus、MinIO 调试端口默认只绑定宿主机 `127.0.0.1`。
- Web 后端保持单个 Uvicorn worker。

## 3. 当前数据基线

设计时工作区中的数据基线如下，打包脚本必须在每次执行时重新统计并生成清单，不能把这些数字写死为校验条件：

| 来源 | 当前文件数 | 当前 chunk 数 | 目标 collection |
| --- | ---: | ---: | --- |
| `parsed/*/chunks.jsonl` | 101 | 约 2,631 | 案例库 |
| `parsed/chunk/*.chunks.jsonl` | 977 | 约 16,299 | 课程库 |

课程数据文件名为 `*.chunks.jsonl`，现有 `scripts/index_parsed.sh` 只查找名为 `chunks.jsonl` 的文件，因此不能直接用同一次扫描把两类数据写入同一个 collection。离线初始化必须显式分开两个输入集合和两个目标 collection。

## 4. 方案选择

### 4.1 采用方案：完整离线包 + 一键部署脚本

离线包同时包含应用与依赖镜像、离线 Compose、环境变量模板、两类 chunks、部署脚本、验证脚本和部署文档。该方案最符合首次交付时“拿到一个包即可部署”的要求，且当前 chunk 数据约百兆，不需要为了传输体量拆包。

### 4.2 未采用方案

- **镜像包与 chunks 数据包分离：** 更适合频繁独立更新应用或知识库，但首次交付步骤更多，本次不采用。
- **迁移预填充 Milvus 数据卷：** 首次启动快，但对 Milvus 版本、Docker 卷名、平台和备份恢复方式更敏感，排错成本高，本次不采用。

## 5. 交付文件

仓库新增或修改以下文件：

| 文件 | 职责 |
| --- | --- |
| `docker-compose.offline.yml` | 离线服务器运行编排，只引用明确镜像名，不包含 `build:` |
| `.env.offline.example` | 私有环境配置模板，默认 Web 端口为 `18088` |
| `scripts/package_web_offline.sh` | 在联网打包机上按 `amd` 或 `arm` 构建和导出完整离线包 |
| `scripts/deploy_web_offline.sh` | 在离线服务器上校验、导入镜像、自动入库并启动完整服务 |
| `scripts/verify_web_offline.sh` | 检查容器、双 collection、API 与 Web 状态 |
| `scripts/index_parsed_offline.sh` | 严格区分案例 chunks 和课程 chunks，分别执行重建与增量入库 |
| `docs/Web问答界面离线部署文档.md` | 面向交付与运维人员的完整中文部署手册 |
| `tests/test_web_offline_deployment.py` | 离线 Compose 与 Shell 脚本契约测试 |

打包后压缩包内部结构：

```text
xhbx-rag-web-offline-amd64/
├── images.tar
├── images.sha256
├── package-manifest.txt
├── docker-compose.offline.yml
├── .env.offline.example
├── parsed/
│   ├── <case-directory>/chunks.jsonl
│   └── chunk/*.chunks.jsonl
├── deploy_web_offline.sh
├── verify_web_offline.sh
├── scripts/
│   └── index_parsed_offline.sh
└── README.offline.md
```

ARM64 包目录名和压缩包名使用 `arm64`，AMD64 使用 `amd64`：

```text
dist/xhbx-rag-web-offline-amd64.tar.gz
dist/xhbx-rag-web-offline-arm64.tar.gz
```

## 6. 打包流程

联网打包机执行：

```bash
sh scripts/package_web_offline.sh amd
```

或：

```bash
sh scripts/package_web_offline.sh arm
```

打包脚本按顺序执行：

1. 只接受 `amd` 或 `arm`，转换成 Docker 平台 `linux/amd64` 或 `linux/arm64`。
2. 检查 Docker、Docker Buildx 和 Docker Compose 可用。
3. 检查案例 chunks 与课程 chunks 均至少存在一个非空文件。
4. 根据目标平台构建 `xhbx-rag-api:latest` 和 `xhbx-rag-web:latest`。
5. 根据目标平台拉取固定版本的 etcd、MinIO、Milvus 镜像。
6. 检查所有待导出镜像的 OS/Architecture 与目标平台一致。
7. 用 `docker save` 生成 `images.tar`，并写入 SHA-256 校验文件。
8. 只复制两类 chunk JSONL，保留其相对目录结构；不复制 `data/`、`.local/`、`generated/` 和其他 parsed 辅助产物。
9. 生成 `package-manifest.txt`，记录平台、镜像列表、两类文件数、两类 chunk 行数和数据文件校验和。
10. 复制离线运行文件并生成最终 `.tar.gz`。

任何一步失败都不保留一个看似完整的最终压缩包。临时包目录允许保留用于排错，但正式压缩包只在所有校验成功后生成。

## 7. 离线 Compose 架构

`docker-compose.offline.yml` 包含五个常驻服务和一个按需工具服务：

| 服务 | 镜像 | 职责 |
| --- | --- | --- |
| `web` | `xhbx-rag-web:latest` | Nginx 托管 React 前端并反代 `/api/` |
| `api` | `xhbx-rag-api:latest` | FastAPI/Uvicorn 问答与文档入库接口 |
| `standalone` | `milvusdb/milvus:v2.6.19` | Milvus standalone |
| `etcd` | `quay.io/coreos/etcd:v3.5.25` | Milvus 元数据 |
| `minio` | `minio/minio:RELEASE.2024-12-18T13-15-44Z` | Milvus 对象存储 |
| `cli` | `xhbx-rag-api:latest` | 一次性入库与验证命令，不常驻 |

Compose 不保留任何 `build:` 字段。部署命令统一使用 `--no-build`，服务器不会尝试从源码构建。Web 默认绑定 `${WEB_PORT:-18088}:80`；API、Milvus、Milvus 健康端口、MinIO API 与 Console 只绑定 `127.0.0.1`。

持久化设计：

- `./data:/app/data`：部署脚本创建空目录，供后续 Web 上传和运行使用，不包含现有原始资料。
- `./.local:/app/.local`：持久化 Web ingestion、批量任务、bad case 和索引写锁。
- `./parsed:/app/parsed:ro`：只读提供离线初始化数据。
- Docker named volumes：持久化 etcd、MinIO 和 Milvus 数据。

## 8. 首次部署与自动入库

离线服务器解压后先准备配置：

```bash
cp .env.offline.example .env
```

填写 Chat、Embedding、Rerank 三类内网服务地址、模型名和 Key。当前配置加载器要求三个 Key 字段非空；如果私有模型服务不校验 Key，使用约定占位值 `not-required`。

首次部署执行：

```bash
sh deploy_web_offline.sh
```

部署脚本按顺序执行：

1. 校验当前 Linux 架构与包中平台声明一致。
2. 校验 `images.tar` 的 SHA-256。
3. 检查 Docker Compose 可用并解析离线 Compose。
4. 检查 `.env` 必需字段非空，不打印 Key 内容。
5. `docker load -i images.tar`。
6. 创建空 `data/` 与 `.local/` 运行目录。
7. 启动 etcd、MinIO、Milvus 并等待健康检查通过。
8. 运行案例库初始化：第一个案例文件使用 `rebuild`，其余文件使用 `incremental`。
9. 运行课程库初始化：第一个课程文件使用 `rebuild --collection course`，其余文件使用 `incremental --collection course`。
10. 两库全部成功后启动 API 与 Web。
11. 执行最终验证脚本，包括一次真实问答冒烟请求；所有检查成功后才输出部署成功和访问地址。

入库是首次部署编排的一部分，不放进 API 容器入口命令。因此日常 `restart` 或普通 `up -d --no-build` 不会重新调用 Embedding 或清空 collection。

## 9. 失败处理与可恢复性

- **平台不匹配、镜像损坏、缺少 Compose：** 在导入镜像和启动服务之前失败。
- **缺少配置：** 在启动基础服务之前失败；错误只显示缺少的变量名。
- **Milvus 未健康：** 不开始入库，输出 `standalone`、`etcd`、`minio` 日志命令。
- **Embedding 服务不可达或响应不兼容：** 当前 collection 初始化失败，API/Web 不启动。
- **Chunk JSONL 非法：** 当前 collection 初始化失败，错误保留具体相对文件路径，API/Web 不启动。
- **课程库失败：** 即使案例库已经完成，整体部署仍失败，不输出成功状态。
- **Chat 或 Rerank 配置错误：** API 配置状态或真实问答冒烟检查失败，整体部署不输出成功状态。冒烟问题默认使用“保单整理有什么作用？”，允许通过 `SMOKE_QUERY` 覆盖。

失败后修复 `.env` 或数据文件，再次执行 `sh deploy_web_offline.sh`。脚本会重新从 `rebuild` 开始构建两个 collection，避免把上次的半成品当作成功结果。

## 10. 日常运行与更新边界

普通启动：

```bash
docker compose -f docker-compose.offline.yml up -d --no-build
```

普通停止且保留数据：

```bash
docker compose -f docker-compose.offline.yml down
```

仅更新应用镜像时，导入新镜像后只重建 `api` 和 `web`，不重建 collection。更新 chunks 时重新执行完整部署脚本，明确重建案例库和课程库。

只有确认要清空向量库时才允许执行带 `-v` 的停止命令。部署文档必须把 `docker compose down -v` 标为破坏性命令，并与普通停止命令分开说明。

## 11. 验证策略

### 11.1 仓库级自动化测试

- Shell 语法检查覆盖四个新增脚本。
- 打包参数测试覆盖 `amd`、`arm` 和非法参数。
- 测试确认离线 Compose 不含 `build:`，包含明确镜像名，默认 Web 端口为 `18088`。
- 测试确认案例扫描只匹配 `parsed/*/chunks.jsonl`，不会把 `parsed/chunk` 写进案例库。
- 测试确认课程扫描匹配 `parsed/chunk/*.chunks.jsonl`，并向 CLI 传入 `--collection course`。
- 测试确认部署脚本只在双库初始化成功后启动 `api` 和 `web`。
- 运行现有 Python 测试、Web 测试与 Web 构建。

### 11.2 部署后验证

`verify_web_offline.sh` 检查：

1. etcd、MinIO、Milvus、API、Web 五个常驻服务均运行且健康。
2. 案例库与课程库均存在且不是空 collection。
3. `http://127.0.0.1:${API_PORT}/api/status` 返回成功配置状态。
4. `http://127.0.0.1:${WEB_PORT}/` 可访问。
5. 向 `/api/answer` 提交 `SMOKE_QUERY`，以一次真实问答贯通 Chat、Embedding、Milvus、Rerank 和答案生成链路。
6. 输出用户访问地址 `http://<服务器IP>:${WEB_PORT}/` 和按服务查看日志的命令。

正式离线包可能达到数 GB。默认实现交付经过测试的打包工具与部署文档，不在开发机自动执行完整多架构镜像构建；需要生成交付包时，由联网打包机明确执行对应平台命令。

## 12. 安全与能力边界

- 离线包不包含真实模型 Key；Key 只存在于服务器 `.env`。
- Web 端口只应暴露到可信内网或受控网关。
- API、Milvus、MinIO 不直接暴露到用户网络。
- 当前 Compose 内部服务仍沿用仓库现有的 MinIO 配置，依赖 Docker 私有网络隔离；若客户要求服务间强鉴权，需要另立安全加固任务。
- 不携带现有 `data/` 后，问答、检索、chunk 引用内容仍可工作，但服务器无法打开原始来源文件，也不能仅凭离线包重新解析原文件。
- chunk 引用中的历史绝对路径可能来自原加工环境，只作为元数据展示，不代表目标服务器存在对应文件。
- 本次不部署模型权重、不部署模型推理服务、不部署外部网关和 TLS 证书。

## 13. 文档交付内容

`docs/Web问答界面离线部署文档.md` 必须提供可直接复制执行的命令，并包含：

1. 联网打包机和离线服务器前置条件。
2. 目标架构确认与 `amd/arm` 打包命令。
3. 离线包传输、解压和 SHA-256 校验。
4. 三类内网模型配置示例和 URL 口径。
5. 首次部署、自动双库入库和预计耗时影响因素。
6. 服务、双 collection、API、Web 的验证命令。
7. 启停、日志、仅应用更新、chunks 更新流程。
8. named volumes 与 `.local` 的备份恢复说明。
9. 模型不可达、架构不符、镜像损坏、Milvus 不健康、入库中断等排错步骤。
10. `data/` 未交付导致的来源文件限制和安全边界。
