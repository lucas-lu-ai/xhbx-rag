# xhbx-rag Kubernetes 无镜像仓库离线部署设计

## 1. 目标

把当前仓库的完整 Web 问答服务部署到无法访问外网、且没有内部镜像仓库的 Kubernetes 集群。所有镜像和初始化知识数据在联网构建机生成一个离线压缩包，传入内网后直接导入指定工作节点的容器运行时。

交付物必须包含可执行打包脚本、节点镜像导入脚本、Kubernetes 清单和中文部署文档，并与当前统一知识库实现一致。

## 2. 已确认约束

- 内网 Kubernetes 集群不能访问外网。
- 内网没有 Docker Registry、Harbor 或其他镜像仓库。
- 默认容器运行时为 containerd，同时记录 Docker 与 CRI-O 的导入方法。
- 目标节点架构只能是 `linux/amd64` 或 `linux/arm64`，必须按实际架构分别打包。
- Kubernetes Pod 使用本地镜像，统一设置 `imagePullPolicy: Never`。
- 所有服务固定调度到带有 `xhbx-rag/offline=true` 标签、且已经导入镜像的工作节点。
- API 必须保持单副本。当前 Web 入库任务、批量任务、SQLite 状态和文件锁不支持多个 API 副本并发写同一知识库。
- 生产检索只读取 `MILVUS_COLLECTION=xhbx_knowledge_chunks` 指定的统一物理 collection。
- 离线包包含 `parsed/`，不包含约 153GB 的仓库 `data/` 原始资料，也不包含模型权重。

## 3. 方案比较与选择

### 方案 A：指定离线工作节点

将完整镜像包导入一个指定工作节点，给该节点添加 `xhbx-rag/offline=true` 标签，所有工作负载通过 `nodeSelector` 固定到该节点。

优点是步骤少、镜像位置确定、无需引入新基础设施。缺点是节点故障后不能自动漂移。本项目当前 API 单副本、Milvus Standalone 本身也不是高可用形态，因此采用此方案。

### 方案 B：导入全部候选节点

在多个候选节点导入相同镜像包并添加相同标签。Pod 可以重新调度，但每次升级都要更新每个节点，且持久卷必须支持跨节点重新挂载。

该方案作为扩展操作写入部署文档，不作为默认部署路径。

### 不采用：集群内临时镜像仓库

临时仓库仍然需要额外的存储、证书、域名和维护流程，与“无镜像仓库、直接上传离线包”的约束不符。

## 4. 离线包结构

联网构建机执行：

```bash
sh dev/package_k8s_offline.sh amd
```

或：

```bash
sh dev/package_k8s_offline.sh arm
```

输出结构：

```text
dist/xhbx-rag-k8s-offline-amd64.tar.gz
└── xhbx-rag-k8s-offline-amd64/
    ├── images.tar
    ├── images.sha256
    ├── package-manifest.txt
    ├── import_k8s_images.sh
    ├── Kubernetes离线部署文档.md
    └── k8s/
        ├── 00-namespace.yaml
        ├── 01-config.yaml
        ├── 02-storage.yaml
        ├── 03-infrastructure.yaml
        ├── 04-index-job.yaml
        ├── 05-application.yaml
        └── 06-access.yaml
```

`images.tar` 包含以下本地镜像标签：

- `localhost/xhbx-rag-api:offline`
- `localhost/xhbx-rag-web:offline`
- `localhost/xhbx-rag-data:offline`
- `localhost/xhbx-rag-etcd:v3.5.25`
- `localhost/xhbx-rag-minio:RELEASE.2024-12-18T13-15-44Z`
- `localhost/xhbx-rag-milvus:v2.6.19`

知识数据镜像基于 API 镜像构建，包含 `/seed/parsed`。索引 Job 通过 init container 把数据复制到共享 `emptyDir`，主容器再执行 `normalize-knowledge` 和 `index-dir --mode rebuild`。服务器侧不需要执行 `kubectl cp`。

## 5. Kubernetes 拓扑

命名空间统一为 `xhbx-rag`。

常驻工作负载：

| 组件 | Kubernetes 类型 | 副本 | 持久化 | 暴露范围 |
| --- | --- | ---: | --- | --- |
| etcd | StatefulSet | 1 | 10Gi PVC | ClusterIP |
| MinIO | StatefulSet | 1 | 50Gi PVC | ClusterIP |
| Milvus Standalone | StatefulSet | 1 | 50Gi PVC | ClusterIP |
| API | Deployment，`Recreate` | 1 | `data` 100Gi、`.local` 10Gi | ClusterIP |
| Web | Deployment | 1 | 无 | ClusterIP、NodePort、可选 Ingress |

首次入库使用 `batch/v1 Job`，执行成功后不再常驻。

模型服务在本集群之外或其他命名空间内提供，Chat、Embedding、Rerank 的地址和模型名放入 ConfigMap，Key 与 MinIO 凭据放入 Secret。离线包只提供无鉴权占位值和必须替换的 MinIO 密码，不携带真实凭据。

## 6. 部署顺序与失败边界

部署按以下顺序执行：

1. 校验离线包和 `images.tar` 的 SHA-256。
2. 在目标工作节点执行 `ctr -n k8s.io images import images.tar`。
3. 给同一节点添加 `xhbx-rag/offline=true` 标签。
4. 创建 Namespace、ConfigMap、Secret 和 PVC。
5. 启动 etcd、MinIO、Milvus，等待三个 StatefulSet 就绪。
6. 创建索引 Job，等待其成功完成。
7. 只有索引 Job 成功后才创建 API 和 Web。
8. 检查 Pod、PVC、统一 collection、API 状态、Web 页面和真实问答。

索引命令使用当前原子重建流程。输入预检、Embedding、staging collection 写入和数量校验任一步失败时，命令返回非零；部署文档不会指示用户继续启动应用。重新执行索引前先删除旧 Job 对象，再重新创建。

普通应用镜像升级只滚动更新 API/Web，不自动重建知识库。知识数据变化时才重新构建数据镜像并执行索引 Job。

## 7. 访问和安全边界

- 默认兜底访问地址为 `http://<节点IP>:30088`。Kubernetes 默认 NodePort 范围不支持 `18088`。
- 已有 Ingress Controller 时可使用 `xhbx-rag.internal.example`，部署前替换为真实内网域名。
- API、Milvus、etcd、MinIO 不通过 NodePort 暴露。
- 当前应用没有内置的企业身份认证层；生产环境应由 Ingress、内网网关或访问控制设备承担 TLS 与认证。
- Milvus 沿用仓库 Compose 的 `seccomp: unconfined` 要求；启用了严格 Pod Security Admission 的集群需要为该命名空间或工作负载配置例外。
- PVC 使用默认 StorageClass。集群没有默认 StorageClass 时，部署前给每个 PVC 填写实际的 `storageClassName`。

## 8. 验收条件

- 所有 YAML 能被 YAML 解析器读取。
- 所有 Pod 模板都包含离线节点 `nodeSelector`。
- 所有容器和 init container 都使用 `localhost/` 镜像并设置 `imagePullPolicy: Never`。
- API 副本数为 1，更新策略为 `Recreate`。
- 索引 Job 先复制数据，再规范化并原子重建 `xhbx_knowledge_chunks`。
- NodePort 固定为 `30088`，API 和基础设施服务仅为 ClusterIP。
- 打包脚本对错误架构参数返回非零，且使用显式镜像参数执行 `docker save`。
- 导入脚本明确把 containerd 镜像导入 `k8s.io` 命名空间。
- 文档覆盖首次部署、验证、日志、升级、重新入库、备份、回滚和彻底卸载的风险边界。
