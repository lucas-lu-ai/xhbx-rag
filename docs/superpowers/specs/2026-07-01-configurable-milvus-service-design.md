# 可配置 Milvus 服务设计

日期：2026-07-01

## 背景

项目当前默认使用 Milvus Lite，本地数据库路径来自 `MILVUS_LITE_PATH`。CLI 和 Web 服务分别直接实例化 `MilvusLiteStore`，Web 还对本地 Lite 索引被占用的异常做了安全错误提示。

现在需要新增 Docker 部署的本地 Milvus 服务，并允许通过配置选择使用 Lite 或 Docker 服务。现有 Lite 行为必须保持默认不变。

## 目标

- 默认继续使用 Milvus Lite，避免破坏现有开发和测试流程。
- 通过显式配置选择 Lite 或 Docker Milvus。
- CLI、Web 问答和状态接口共用同一套 Milvus store 创建逻辑。
- Docker Milvus 使用 `pymilvus.MilvusClient` 连接，不复制 collection/schema/search 逻辑。
- Web 状态接口展示当前 Milvus 模式、collection 和连接目标。
- 继续保留 Lite 本地索引被占用时的安全错误提示，避免泄露本机路径或 token。

## 非目标

- 不在本次改动中编排或启动 Docker 容器。
- 不迁移已有 Lite 数据。
- 不改变 collection schema、向量索引类型、检索逻辑或 rerank 逻辑。
- 不新增认证管理界面。

## 配置

新增和保留的环境变量如下：

```env
MILVUS_MODE=lite
MILVUS_LITE_PATH=.local/milvus/xhbx_rag.db
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=
MILVUS_COLLECTION=xhbx_sales_chunks
MILVUS_VECTOR_DIM=
```

配置规则：

- `MILVUS_MODE` 支持 `lite` 和 `docker`，默认是 `lite`。
- `lite` 模式使用 `MILVUS_LITE_PATH` 创建本地 Milvus Lite 连接。
- `docker` 模式使用 `MILVUS_URI` 创建远程 Milvus 连接。
- `MILVUS_TOKEN` 可选；为空时不传 token，便于连接默认未启用认证的本地 Docker Milvus。
- `MILVUS_COLLECTION` 和 `MILVUS_VECTOR_DIM` 行为保持不变。
- 未知的 `MILVUS_MODE` 应抛出 `ConfigError`，提示允许值。

## 架构

保留当前 `MilvusLiteStore` 兼容名，同时在内部支持通用连接参数。

设计上新增一个 store 工厂函数，例如 `create_milvus_store(config)`：

- `config.milvus_mode == "lite"` 时，创建本地路径连接。
- `config.milvus_mode == "docker"` 时，创建 Docker Milvus URI 连接。
- CLI 和 Web 不再自行组装 `MilvusLiteStore` 参数，而是调用同一个工厂。

`MilvusStore` 的 collection 创建、upsert、search、keyword_search 和 drop collection 行为保持一致。因为 `MilvusClient` 对 Lite 路径和远程 URI 暴露同一套接口，核心检索代码不需要分叉。

## 数据流

CLI 入库和检索：

1. `RetrievalConfig.from_env()` 读取 Milvus 模式和连接配置。
2. `_milvus_store(config)` 调用统一工厂。
3. `index_chunks`、`search_evidence`、`answer_query` 使用返回的 store。

Web 问答：

1. `answer_question()` 读取配置。
2. 通过统一工厂创建 store。
3. 其余 RAG 组件和回答流程保持不变。
4. 最终统一关闭资源。

Web 状态：

1. `get_status()` 返回 `milvus_mode`、`milvus_collection` 和连接目标。
2. Lite 模式连接目标是 Lite path。
3. Docker 模式连接目标是 URI。
4. 不返回 token。

## 错误处理

- 配置缺失或非法模式使用现有 `ConfigError` 机制。
- `MILVUS_VECTOR_DIM` 解析失败继续走现有安全配置解析错误。
- Lite 模式创建 store 时，如果遇到 `Open local milvus failed`，Web 继续返回现有本地索引不可用提示。
- Docker 连接失败不复用 Lite 锁错误文案；让上层按普通异常处理，避免误导用户去关闭本地索引进程。

## 测试计划

新增或调整以下测试：

- `tests/test_config.py`
  - 默认仍为 Lite。
  - 能读取 Docker 模式的 `MILVUS_URI` 和 `MILVUS_TOKEN`。
  - 非法 `MILVUS_MODE` 抛出 `ConfigError`。
  - `safe_summary()` 不包含 token。
- `tests/test_cli_retrieval.py`
  - CLI store 工厂在 Lite 模式下传 Lite path。
  - CLI store 工厂在 Docker 模式下传 URI/token。
- `tests/test_web_services.py`
  - `get_status()` 返回 Milvus 模式和连接目标。
  - `answer_question()` 使用统一工厂创建 store。
  - Lite 本地索引打开失败继续被安全文案替换。
- `tests/test_milvus_store.py`
  - 保留 Lite 端到端 round trip 测试。
  - 用 monkeypatch 验证远程 URI/token 会传给 `MilvusClient`，避免依赖真实 Docker 服务。

## 文档更新

README 的环境变量说明需要补充：

- Lite 默认配置。
- Docker Milvus 配置示例。
- 说明本次只选择连接方式，不负责启动 Docker 服务。

## 自检

- 没有未决项或未完成项。
- 方案只影响 Milvus 配置和创建入口，不触碰 schema、检索、rerank 或前端证据展示。
- 默认值保持 Lite，因此现有用户不设置新变量时行为不变。
- Docker 模式的认证配置可选，适配本地未启用认证和启用 token 的两种场景。
