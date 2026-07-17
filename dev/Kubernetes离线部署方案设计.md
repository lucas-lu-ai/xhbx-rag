# xhbx-rag Kubernetes 无镜像仓库离线部署设计

## 1. 目标

把当前仓库的完整 Web 问答服务部署到无法访问外网、且没有内部镜像仓库的 Kubernetes 集群。所有镜像和初始化知识数据在联网构建机生成一个离线压缩包，传入内网后直接导入指定工作节点的容器运行时。

交付物包含可执行打包脚本、节点镜像导入脚本、Kubernetes 清单和中文部署文档，并与当前统一知识库实现一致。

## 2. 已确认约束

- 内网 Kubernetes 集群不能访问外网，也没有 Docker Registry、Harbor 或其他镜像仓库。
- 默认容器运行时为 containerd，同时记录 Docker 与 CRI-O 的导入方法。
- 目标节点架构只能是 `linux/amd64` 或 `linux/arm64`，必须按实际架构打包。
- Pod 使用本地镜像，统一设置 `imagePullPolicy: Never`。
- 所有服务固定调度到带有 `xhbx-rag/offline=true` 标签、且已经导入镜像的工作节点。
- API 保持单副本。当前 Web 入库任务、批量任务、SQLite 状态和文件锁不支持多个 API 副本并发写同一知识库。
- 生产检索只读取 `MILVUS_COLLECTION=xhbx_knowledge_chunks` 指定的统一物理 collection。
- 离线包包含 `parsed/` 知识数据，不包含仓库 `data/` 原始资料，也不包含模型权重。

## 3. 节点部署方式

默认采用“指定离线工作节点”：把完整镜像包导入一个工作节点，给该节点添加 `xhbx-rag/offline=true` 标签，所有工作负载通过 `nodeSelector` 固定到该节点。

这个方案不提供节点故障自动迁移。如果需要多个候选节点，必须在每个节点导入同一镜像包、添加相同标签，并确保 PVC 可以在节点间重新挂载。当前 API 单副本、Milvus Standalone 本身也不是高可用形态，因此首版不引入集群内临时镜像仓库或多副本设计。

## 4. 离线包

联网构建机执行：

```bash
sh dev/package_k8s_offline.sh amd
```

或：

```bash
sh dev/package_k8s_offline.sh arm
```

生成：

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

`images.tar` 包含：

- `localhost/xhbx-rag-api:offline`
- `localhost/xhbx-rag-web:offline`
- `localhost/xhbx-rag-data:offline`
- `localhost/xhbx-rag-etcd:v3.5.25`
- `localhost/xhbx-rag-minio:RELEASE.2024-12-18T13-15-44Z`
- `localhost/xhbx-rag-milvus:v2.6.19`

知识数据镜像基于固定多架构镜像 `busybox:1.36.1` 构建，包含 `/seed/parsed`。索引 Job 通过 init container 把数据复制到共享 `emptyDir`，主容器执行 `normalize-knowledge` 和 `index-dir --mode rebuild`，服务器侧不需要执行 `kubectl cp`。

## 5. Kubernetes 拓扑

命名空间统一为 `xhbx-rag`。

| 组件 | 类型 | 副本 | 持久化 | 暴露范围 |
| --- | --- | ---: | --- | --- |
| etcd | StatefulSet | 1 | 10Gi PVC | ClusterIP |
| MinIO | StatefulSet | 1 | 50Gi PVC | ClusterIP |
| Milvus Standalone | StatefulSet | 1 | 50Gi PVC | ClusterIP |
| API | Deployment，`Recreate` | 1 | `data` 100Gi、`.local` 10Gi | ClusterIP |
| Web | Deployment | 1 | 无 | ClusterIP、NodePort、可选 Ingress |

首次入库使用 `batch/v1 Job`，成功后不再常驻。

模型服务由内网另行提供。Chat、Embedding、Rerank 的地址和模型名放入 ConfigMap，Key 与 MinIO 凭据放入 Secret。离线包不携带真实凭据。

## 6. 部署与失败边界

部署顺序：

1. 校验离线包和 `images.tar` 的 SHA-256。
2. 在目标工作节点执行 `ctr -n k8s.io images import images.tar`。
3. 给该节点添加 `xhbx-rag/offline=true` 标签。
4. 创建 Namespace、ConfigMap、Secret 和 PVC。
5. 启动 etcd、MinIO、Milvus并等待就绪。
6. 创建索引 Job 并等待成功。
7. 只有索引 Job 成功后才创建 API 和 Web。
8. 检查 Pod、PVC、统一 collection、API 状态、Web 页面和真实问答。

索引使用当前原子重建流程。输入预检、Embedding、staging collection 写入和数量校验任一步失败时命令返回非零；此时不能继续启动应用。重新执行索引前先删除旧 Job 对象。

普通应用镜像升级不自动重建知识库；只有知识数据变化时才重新构建数据镜像并执行索引 Job。

## 7. 访问和安全边界

- 兜底访问地址为 `http://<节点IP>:30088`。Kubernetes 默认 NodePort 范围不支持 `18088`。
- 已有 Ingress Controller 时可使用内网域名，并在部署前替换示例域名。
- API、Milvus、etcd、MinIO 不通过 NodePort 暴露。
- 当前应用没有企业身份认证层，生产环境由 Ingress、内网网关或访问控制设备承担 TLS 与认证。
- Milvus 沿用现有 Compose 的 `seccomp: unconfined` 要求；严格 Pod Security Admission 环境需要配置例外。
- PVC 使用默认 StorageClass；没有默认 StorageClass 时必须填写实际的 `storageClassName`。

## 8. 验收条件

- 所有 YAML 均能正常解析。
- 所有 Pod 模板都有离线节点 `nodeSelector`。
- 所有容器和 init container 都使用 `localhost/` 镜像并设置 `imagePullPolicy: Never`。
- API 副本数为 1，更新策略为 `Recreate`。
- 索引 Job 先复制数据，再规范化并原子重建 `xhbx_knowledge_chunks`。
- NodePort 为 `30088`，API 和基础设施服务仅为 ClusterIP。
- 打包脚本对错误架构参数返回非零，且使用显式镜像参数执行 `docker save`。
- 导入脚本把 containerd 镜像导入 `k8s.io` 命名空间。
- 文档覆盖首次部署、验证、日志、升级、重新入库、备份、回滚和卸载风险边界。
