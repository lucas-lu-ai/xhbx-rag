# xhbx-rag Web 界面 Kubernetes 完全离线部署文档

本文档用于把当前仓库的完整 Web 问答界面部署到无法访问外网、没有内部镜像仓库的 Kubernetes 集群。

部署方式不是让 Kubernetes 拉取镜像，而是：在联网机器构建全部镜像和知识数据镜像，生成一个离线包，把离线包传到内网，在指定工作节点的容器运行时中直接导入，再使用 `imagePullPolicy: Never` 启动服务。

## 1. 最短部署路径

联网构建机：

```bash
cd /path/to/xhbx-rag
sh dev/package_k8s_offline.sh amd
```

ARM64 集群改为：

```bash
sh dev/package_k8s_offline.sh arm
```

把生成的压缩包传入内网，解压后在目标 Kubernetes 工作节点执行：

```bash
sh import_k8s_images.sh containerd images.tar
```

在能操作集群的终端执行：

```bash
kubectl label node <节点名> xhbx-rag/offline=true --overwrite
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-config.yaml
kubectl apply -f k8s/02-storage.yaml
kubectl apply -f k8s/03-infrastructure.yaml
```

等待基础设施就绪后初始化知识库：

```bash
kubectl apply -f k8s/04-index-job.yaml
kubectl wait --for=condition=complete job/xhbx-rag-index -n xhbx-rag --timeout=6h
```

索引成功后再启动应用：

```bash
kubectl apply -f k8s/05-application.yaml
kubectl apply -f k8s/06-access.yaml
```

Web hostPort 访问地址：

```text
http://<节点IP>:33004
```

以上是执行顺序摘要。首次操作前必须继续阅读环境检查、模型配置、StorageClass 和失败处理章节。

## 2. 架构与部署边界

命名空间固定为 `xhbx-rag`，包含以下资源：

| 组件 | Kubernetes 资源 | 副本 | 镜像 | 作用 |
| --- | --- | ---: | --- | --- |
| etcd | StatefulSet + ClusterIP | 1 | `localhost/xhbx-rag-etcd:v3.5.25` | Milvus 元数据 |
| MinIO | StatefulSet + ClusterIP | 1 | `localhost/xhbx-rag-minio:RELEASE.2024-12-18T13-15-44Z` | Milvus 对象存储 |
| Milvus | StatefulSet + ClusterIP | 1 | `localhost/xhbx-rag-milvus:v2.6.19` | 向量数据库 |
| 索引任务 | Job | 一次性 | API + 数据镜像 | 规范化 chunk 并原子重建统一知识库 |
| API | Deployment + ClusterIP | 1 | `localhost/xhbx-rag-api:offline` | FastAPI/Uvicorn 问答和 Web 入库服务 |
| Web | Deployment + ClusterIP | 1 | `localhost/xhbx-rag-web:offline` | Nginx 托管前端并反代 `/api/` |
| 外部访问 | Web hostPort + Ingress | - | - | 节点 `33004` 直连，Ingress 可选 |

所有 Pod 都包含：

```yaml
imagePullPolicy: Never
nodeSelector:
  xhbx-rag/offline: "true"
```

这意味着只有已经导入镜像、并添加了该标签的节点才能运行服务。

Web 容器把内部端口 `80` 映射到所在节点的 `hostPort: 33004`。之所以不使用 NodePort，是因为 `33004` 超出 Kubernetes 默认 NodePort 范围 `30000–32767`。

### 2.1 为什么 API 只能是一个副本

当前 Web 批量任务、文档入库任务和坏案例记录使用 `.local/` 下的 SQLite/文件状态，collection 写入锁也是同机文件锁。多个 API 副本会让后台 Runner、SQLite 和索引写入失去统一协调，因此清单固定：

```yaml
replicas: 1
strategy:
  type: Recreate
```

不要自行改成多个 API 副本。Web 静态页面可以扩容，但在当前离线单节点方案中没有必要。

### 2.2 不是高可用方案

默认只在一个工作节点导入镜像并固定调度。节点故障时 Pod 不会漂移到没有镜像的节点。Milvus 也是 Standalone，不是分布式高可用部署。

如需多个候选节点，必须同时满足：

1. 每个候选节点都导入相同 `images.tar`。
2. 每个候选节点都添加 `xhbx-rag/offline=true` 标签。
3. StorageClass 支持故障节点解除挂载后在其他节点重新挂载。
4. 仍然保持 API 一个副本。

## 3. 离线包包含和不包含的内容

离线包包含：

- API、Web、etcd、MinIO、Milvus 固定版本镜像。
- `localhost/xhbx-rag-data:offline` 知识数据镜像。
- 当前仓库 `parsed/` 下的知识数据。
- Kubernetes YAML、节点导入脚本、校验值和本文档。

离线包明确：

- 不包含模型权重或 Chat、Embedding、Rerank 推理服务。
- 不包含真实模型 Key。
- 不包含 `data/` 原始 Word、PDF、PPT 等资料。
- 不包含 Kubernetes、containerd、Docker、CRI-O 或 `kubectl` 的安装包。
- 不包含 Ingress Controller 和 StorageClass provisioner。

问答使用的是 `parsed/` chunk 和 Milvus 索引，所以不携带原始 `data/` 不影响已有知识问答，但服务器无法打开或重新解析原始来源文件。

## 4. 资源和网络要求

### 4.1 联网构建机

构建机需要：

- 当前完整仓库和 `parsed/` 数据。
- Docker Engine 或 Docker Desktop。
- Docker Buildx。
- 能访问 Docker Hub、Quay、GitHub Container Registry、Python 和 npm 依赖源。
- `tar`，以及 `sha256sum` 或 `shasum`。
- 建议至少 40GB 可用磁盘，用于构建缓存、六个镜像和离线包。

### 4.2 内网 Kubernetes

需要：

- 有权限执行 `kubectl apply/get/logs/describe/wait/label`。
- 能登录或通过运维通道在目标工作节点执行命令。
- 节点容器运行时为 containerd、Docker 或 CRI-O。
- 集群至少有一个默认 StorageClass，或已知实际的 `storageClassName`。
- Pod 网络能访问内网 Chat、Embedding、Rerank 服务。
- 目标节点建议至少 8 vCPU、32GB 内存、200GB 以上可分配持久化空间。
- 如果模型服务不在该节点运行，K8s 节点不需要 GPU。

默认 PVC：

| PVC | 容量 | 用途 |
| --- | ---: | --- |
| `etcd-data` | 10Gi | etcd 元数据 |
| `minio-data` | 50Gi | Milvus 对象数据 |
| `milvus-data` | 50Gi | Milvus 本地状态 |
| `api-data` | 100Gi | Web 后续上传和处理资料 |
| `api-local` | 10Gi | SQLite、任务状态、锁和坏案例 |

这些是初始建议，不是实际容量上限。上线前应按新增资料量、备份周期和 StorageClass 扩容能力调整；PVC 通常可以扩容但不能缩容。

## 5. 确认集群节点架构和运行时

查看节点、架构和容器运行时：

```bash
kubectl get nodes \
  -o custom-columns='NAME:.metadata.name,ARCH:.status.nodeInfo.architecture,RUNTIME:.status.nodeInfo.containerRuntimeVersion'
```

架构映射：

| Kubernetes `ARCH` | 打包参数 | Docker 平台 |
| --- | --- | --- |
| `amd64` | `amd` | `linux/amd64` |
| `arm64` | `arm` | `linux/arm64` |

也可以在目标节点执行：

```bash
uname -m
```

`x86_64` 对应 `amd`，`aarch64` 对应 `arm`。

混合架构集群必须选择一个确定的离线节点并按该节点架构打包。不要把 AMD64 镜像导入 ARM64 节点，反之亦然。

## 6. 在联网机器生成离线包

进入仓库根目录：

```bash
cd /path/to/xhbx-rag
```

AMD64：

```bash
sh dev/package_k8s_offline.sh amd
```

输出：

```text
dist/xhbx-rag-k8s-offline-amd64.tar.gz
```

ARM64：

```bash
sh dev/package_k8s_offline.sh arm
```

输出：

```text
dist/xhbx-rag-k8s-offline-arm64.tar.gz
```

打包脚本会：

1. 校验目标架构参数。
2. 检查 `parsed/` 中存在 chunk JSONL。
3. 为目标架构构建 API 和 Web 镜像。
4. 创建独立数据构建上下文，把 `parsed/` 封装进知识数据镜像。
5. 拉取固定版本 etcd、MinIO、Milvus 并重标记为 `localhost/` 镜像。
6. 校验六个镜像的 OS/Architecture。
7. 显式导出六个镜像到 `images.tar`。
8. 生成 `images.sha256` 和 `package-manifest.txt`。
9. 打入 Kubernetes YAML、导入脚本和部署文档。

打包过程需要外网；最终压缩包在内网运行时不需要任何镜像仓库。

## 7. 传输、解压和校验

通过客户允许的离线介质、堡垒机文件通道或内网文件服务，把与节点架构匹配的压缩包传入内网。

解压 AMD64 包：

```bash
tar -xzf xhbx-rag-k8s-offline-amd64.tar.gz
cd xhbx-rag-k8s-offline-amd64
```

ARM64 时替换为 `arm64` 文件名和目录名。

查看包清单：

```bash
cat package-manifest.txt
```

校验镜像包：

```bash
sha256sum images.tar
cat images.sha256
```

macOS 或没有 `sha256sum` 的环境使用：

```bash
shasum -a 256 images.tar
cat images.sha256
```

两个 SHA-256 必须一致。不一致时停止部署并重新传输，不能继续导入。

## 8. 把镜像导入 Kubernetes 工作节点

离线包必须位于目标工作节点本机文件系统，不能只放在运行 `kubectl` 的管理机上。

### 8.1 containerd，推荐路径

在目标节点执行：

```bash
sh import_k8s_images.sh containerd images.tar
```

脚本实际导入到 containerd 的 Kubernetes 命名空间：

```bash
sudo ctr -n k8s.io images import images.tar
```

检查镜像：

```bash
sudo ctr -n k8s.io images list | grep 'localhost/xhbx-rag-'
```

必须使用 `-n k8s.io`。导入 containerd 默认命名空间后，`ctr images list` 虽然能看到镜像，kubelet 仍可能看不到。

### 8.2 Docker 运行时

```bash
sh import_k8s_images.sh docker images.tar
```

等价命令：

```bash
sudo docker load -i images.tar
```

### 8.3 CRI-O 运行时

```bash
sh import_k8s_images.sh crio images.tar
```

等价命令：

```bash
sudo podman load -i images.tar
```

如果客户 CRI-O 使用独立 storage 配置，应由集群管理员确认 root 用户的 Podman 与 CRI-O 共享镜像存储。

## 9. 标记离线节点

在管理终端找到刚才导入镜像的节点名：

```bash
kubectl get nodes -o wide
```

添加标签：

```bash
kubectl label node <节点名> xhbx-rag/offline=true --overwrite
```

验证：

```bash
kubectl get nodes -l xhbx-rag/offline=true
```

不要给没有导入镜像的节点添加该标签。否则 Pod 可能被调度过去并出现 `ErrImageNeverPull`。

## 10. 修改模型、Secret 和访问配置

所有修改都在解压后的离线包目录中进行。

### 10.1 修改内网模型地址

编辑：

```text
k8s/01-config.yaml
```

至少替换：

```yaml
BASE_URL: http://10.10.10.20:8000/v1
MODEL_NAME: private-chat-model
VISION_MODEL_NAME: private-vision-model
EMBEDDING_BASE_URL: http://10.10.10.21:8000/v1
EMBEDDING_MODEL_NAME: private-embedding-model
RERANK_BASE_URL: http://10.10.10.22:8000/v1
RERANK_MODEL_NAME: private-rerank-model
```

地址规则：

- `BASE_URL` 后会拼接 `/chat/completions`。
- `EMBEDDING_BASE_URL` 后会拼接 `/embeddings`。
- `RERANK_BASE_URL` 后会拼接 `/rerank`。
- 不要把最终接口路径重复写进根地址。
- 如果模型服务在 Kubernetes 节点宿主机，不要写 `127.0.0.1`；Pod 内的回环地址是 Pod 自身，应使用 Pod 可达的宿主机内网 IP 或 Service DNS。

### 10.2 修改凭据

在同一文件的 Secret 中修改：

```yaml
stringData:
  API_KEY: not-required
  EMBEDDING_API_KEY: not-required
  RERANK_API_KEY: not-required
  MINIO_ROOT_USER: xhbx-minio
  MINIO_ROOT_PASSWORD: replace-before-deploy
```

三个模型服务不鉴权时保留 `not-required`，因为当前程序要求 Key 字段非空；需要鉴权时替换为真实内网凭据。

`MINIO_ROOT_PASSWORD` 必须在首次部署前替换为客户环境的随机强密码。不要把修改后的真实 Secret 重新传回联网环境或提交 Git。

### 10.3 StorageClass

检查：

```bash
kubectl get storageclass
```

名称后带 `(default)` 时，当前 PVC 可以直接使用。没有默认 StorageClass 时，在 `k8s/02-storage.yaml` 每个 PVC 的 `spec` 中添加：

```yaml
storageClassName: <客户实际StorageClass名称>
```

必须使用支持 PVC 持久化的存储。不要使用节点重启后会清空的数据目录。

### 10.4 Ingress

hostPort 已在 `k8s/05-application.yaml` 中固定为 `33004`。使用 Ingress 时编辑 `k8s/06-access.yaml`：

```yaml
ingressClassName: nginx
host: xhbx-rag.internal.example
```

替换成客户实际 IngressClass 和内网域名。集群没有 Ingress Controller 时，Ingress 对象不会提供访问能力，直接使用节点 hostPort。

## 11. 创建命名空间、配置和 PVC

执行：

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-config.yaml
kubectl apply -f k8s/02-storage.yaml
```

检查：

```bash
kubectl get configmap,secret,pvc -n xhbx-rag
```

PVC 在使用者 Pod 创建前可能显示 `Pending`，这取决于 StorageClass 的 `volumeBindingMode`。基础设施创建后仍长期 `Pending` 才是异常。

## 12. 启动 etcd、MinIO、Milvus

创建资源：

```bash
kubectl apply -f k8s/03-infrastructure.yaml
```

等待 Pod Ready：

```bash
kubectl wait --for=condition=ready pod -l app=etcd -n xhbx-rag --timeout=5m
kubectl wait --for=condition=ready pod -l app=minio -n xhbx-rag --timeout=5m
kubectl wait --for=condition=ready pod -l app=standalone -n xhbx-rag --timeout=10m
```

检查资源：

```bash
kubectl get pods,pvc,svc -n xhbx-rag -o wide
```

查看日志：

```bash
kubectl logs statefulset/etcd -n xhbx-rag --tail=200
kubectl logs statefulset/minio -n xhbx-rag --tail=200
kubectl logs statefulset/standalone -n xhbx-rag --tail=200
```

三个组件全部 Ready 后才能执行索引 Job。

## 13. 首次初始化统一知识库

先确认 Pod 网络能连接三个模型地址。至少可以从 API 镜像临时 Pod 探测模型服务 TCP/HTTP；由于该临时 Pod 同样只能运行在离线节点，命令必须保留节点选择条件，最简单的方式是直接创建索引 Job并观察第一段连接日志。

确保没有旧 Job：

```bash
kubectl delete job xhbx-rag-index -n xhbx-rag --ignore-not-found
```

创建：

```bash
kubectl apply -f k8s/04-index-job.yaml
```

等待索引 Pod 创建：

```bash
kubectl wait --for=condition=ready pod -l job-name=xhbx-rag-index -n xhbx-rag --timeout=10m
```

实时日志：

```bash
kubectl logs -f job/xhbx-rag-index -n xhbx-rag
```

等待完成：

```bash
kubectl wait --for=condition=complete job/xhbx-rag-index -n xhbx-rag --timeout=6h
```

也可单独查看最近日志：

```bash
kubectl logs job/xhbx-rag-index -n xhbx-rag --tail=300
```

Job 内部执行：

```text
数据镜像复制 /seed/parsed
→ normalize-knowledge 全量预检和规范化
→ index-dir --mode rebuild
→ staging collection 写入
→ 数量、向量维度、chunk ID 校验
→ 切换为 xhbx_knowledge_chunks
```

首次约 18,930 条 chunk 需要调用内网 Embedding 服务。耗时取决于模型吞吐、网络延迟、批量能力和存储性能。

如果 Job 失败：

```bash
kubectl get job,pod -n xhbx-rag
kubectl describe job xhbx-rag-index -n xhbx-rag
kubectl logs job/xhbx-rag-index -n xhbx-rag --all-containers --tail=500
```

在修复模型地址、凭据、向量维度或数据问题前，不要创建 API/Web。清单设置 `backoffLimit: 0`，避免失败后自动重复调用 Embedding。

修复后重跑：

```bash
kubectl delete job xhbx-rag-index -n xhbx-rag
kubectl apply -f k8s/04-index-job.yaml
kubectl wait --for=condition=complete job/xhbx-rag-index -n xhbx-rag --timeout=6h
```

## 14. 启动 API 和 Web

只有索引 Job 显示 `Complete` 后才执行：

先登录目标工作节点检查 TCP `33004` 是否被占用：

```bash
sudo ss -lntp | grep ':33004 ' || true
```

预期无输出。已有进程占用时必须先释放端口，否则 Web Pod 无法启动。集群准入策略还必须允许 `hostPort: 33004`；严格 Pod Security 或自定义策略拒绝 hostPort 时，需要集群管理员为该工作负载配置例外。

确认后创建应用：

```bash
kubectl apply -f k8s/05-application.yaml
```

等待：

```bash
kubectl rollout status deployment/api -n xhbx-rag --timeout=10m
kubectl rollout status deployment/web -n xhbx-rag --timeout=5m
```

如需 Ingress，再创建 Ingress 资源：

```bash
kubectl apply -f k8s/06-access.yaml
```

检查：

```bash
kubectl get deploy,pod,svc,ingress -n xhbx-rag -o wide
```

## 15. 访问方式

### 15.1 节点 hostPort

获取离线节点 IP：

```bash
kubectl get node <节点名> -o wide
```

访问：

```text
http://<节点IP>:33004
```

Web Pod 将容器端口 `80` 直接映射到所在节点 TCP `33004`。集群防火墙、安全组和节点防火墙必须允许批准的内网用户访问 TCP `33004`。

### 15.2 Ingress

确认 Ingress Controller 已运行，并把内网 DNS 指向 Controller 入口：

```bash
kubectl get ingressclass
kubectl get ingress -n xhbx-rag
```

默认示例域名是：

```text
http://xhbx-rag.internal.example
```

生产环境应由 Ingress 或上游内网网关提供 TLS、身份认证和访问控制。应用自身当前不提供企业级登录认证。

## 16. 部署后验证

### 16.1 资源状态

```bash
kubectl get all,pvc,ingress -n xhbx-rag
```

预期：

- etcd、MinIO、Milvus、API、Web Pod 均为 `Running` 且 `READY` 为 `1/1`。
- 索引 Job 为 `Complete`。
- 五个 PVC 为 `Bound`。
- 没有 `ImagePullBackOff` 或 `ErrImageNeverPull`。

### 16.2 API 和 Web

通过节点 hostPort：

```bash
curl -fsS http://<节点IP>:33004/api/status
curl -fsS -o /dev/null http://<节点IP>:33004/
```

`/api/status` 应包含：

```json
{"ok":true}
```

### 16.3 检查统一 collection

```bash
kubectl exec -i deployment/api -n xhbx-rag -- python - <<'PY'
from xhbx_rag.config import RetrievalConfig
from xhbx_rag.milvus_store import create_milvus_store

config = RetrievalConfig.from_env(require_chat=False)
store = create_milvus_store(config, collection_name=config.milvus_collection)
if not store.collection_exists():
    raise SystemExit(f"collection 不存在: {config.milvus_collection}")
stats = store.client.get_collection_stats(collection_name=config.milvus_collection)
row_count = int(stats.get("row_count", 0))
if row_count <= 0:
    raise SystemExit(f"collection 为空: {config.milvus_collection}")
print(f"collection={config.milvus_collection} row_count={row_count}")
PY
```

必须看到 `collection=xhbx_knowledge_chunks` 且 `row_count` 大于 0。

### 16.4 真实问答

```bash
curl -fsS \
  -H 'Content-Type: application/json' \
  -d '{"query":"保单整理有什么作用？","top_n":20,"top_k":5}' \
  http://<节点IP>:33004/api/answer
```

响应必须包含非空 `answer`。这一步同时验证 Chat、Embedding、Milvus、Rerank 和答案生成链路。

## 17. 日常运维命令

查看状态：

```bash
kubectl get pod,deploy,statefulset,job,pvc,svc,ingress -n xhbx-rag -o wide
```

API/Web 日志：

```bash
kubectl logs -f deployment/api -n xhbx-rag --tail=200
kubectl logs -f deployment/web -n xhbx-rag --tail=200
```

基础设施日志：

```bash
kubectl logs -f statefulset/standalone -n xhbx-rag --tail=200
kubectl logs -f statefulset/etcd -n xhbx-rag --tail=200
kubectl logs -f statefulset/minio -n xhbx-rag --tail=200
```

索引日志：

```bash
kubectl logs job/xhbx-rag-index -n xhbx-rag --tail=300
```

重启应用，不重建知识库：

```bash
kubectl rollout restart deployment/api deployment/web -n xhbx-rag
kubectl rollout status deployment/api -n xhbx-rag --timeout=10m
kubectl rollout status deployment/web -n xhbx-rag --timeout=5m
```

普通重启不会调用 Embedding，也不会清空 Milvus。

## 18. 离线升级应用

没有镜像仓库时，建议保留当前和上一版完整离线包。

升级步骤：

1. 在联网机器从新代码重新生成对应架构离线包。
2. 把新包传到内网目标节点。
3. 校验 `images.tar`。
4. 在所有带 `xhbx-rag/offline=true` 标签的节点导入新镜像包。
5. 如果知识数据没有变化，只重启 API/Web。

containerd：

```bash
sh import_k8s_images.sh containerd images.tar
```

应用升级：

```bash
kubectl rollout restart deployment/api deployment/web -n xhbx-rag
kubectl rollout status deployment/api -n xhbx-rag --timeout=10m
kubectl rollout status deployment/web -n xhbx-rag --timeout=5m
```

因为清单使用固定 `offline` 标签和本地镜像，必须先完成节点导入，再重建 Pod。不要先重启 Pod，否则仍会使用旧的本地标签或直接启动失败。

如果 ConfigMap/Secret 或 YAML 有变化，先应用对应文件，再重启：

```bash
kubectl apply -f k8s/01-config.yaml
kubectl apply -f k8s/05-application.yaml
kubectl apply -f k8s/06-access.yaml
```

## 19. 更新知识数据并重新入库

知识数据变化时，重新打包会生成新的 `localhost/xhbx-rag-data:offline`。把新镜像导入节点后，为避免重建期间继续读写，先停止 API/Web：

```bash
kubectl scale deployment/api deployment/web -n xhbx-rag --replicas=0
kubectl delete job xhbx-rag-index -n xhbx-rag --ignore-not-found
kubectl apply -f k8s/04-index-job.yaml
kubectl wait --for=condition=complete job/xhbx-rag-index -n xhbx-rag --timeout=6h
```

确认 Job 成功后恢复：

```bash
kubectl scale deployment/api deployment/web -n xhbx-rag --replicas=1
kubectl rollout status deployment/api -n xhbx-rag --timeout=10m
kubectl rollout status deployment/web -n xhbx-rag --timeout=5m
```

如果 Job 失败，保持 API/Web 为 0，先排查并重跑。不要在索引失败时恢复服务并宣称更新完成。

## 20. 备份建议

生产环境至少保存：

- 当前和上一版完整离线包。
- 已脱敏的 Kubernetes YAML。
- Secret 的安全备份，存入客户凭据管理系统，不放入普通文件共享。
- `api-data`、`api-local`、`etcd-data`、`minio-data`、`milvus-data` 的 CSI VolumeSnapshot 或存储侧快照。

Milvus 数据同时依赖 etcd、MinIO 和 Milvus 本地卷。只复制其中一个 PVC 不能构成可靠恢复点。使用存储快照时，应在维护窗口停止 API 和索引写入，并按客户存储平台的一致性能力执行三个基础设施卷的协调快照。

本离线包没有携带 Milvus Backup 等额外工具镜像；需要逻辑备份时，应另行评估、打包和验证对应版本的备份工具，不能在内网现场临时在线安装。

## 21. 回滚

### 21.1 配置或 Pod 模板回滚

查看历史：

```bash
kubectl rollout history deployment/api -n xhbx-rag
kubectl rollout history deployment/web -n xhbx-rag
```

配置或 Pod 模板发生问题时可以执行：

```bash
kubectl rollout undo deployment/api -n xhbx-rag
kubectl rollout undo deployment/web -n xhbx-rag
```

### 21.2 镜像回滚

由于所有版本都使用固定 `offline` 标签，单独执行 `rollout undo` 不保证恢复旧镜像。镜像回滚必须：

1. 找到上一版离线包。
2. 在所有离线节点重新导入上一版 `images.tar`。
3. 再执行 `kubectl rollout restart`。

```bash
sh import_k8s_images.sh containerd /path/to/previous/images.tar
kubectl rollout restart deployment/api deployment/web -n xhbx-rag
```

### 21.3 知识库回滚

知识库重建成功后，回滚数据需要重新导入包含上一版 `parsed/` 的旧数据镜像并重跑索引 Job。删除 collection 或 PVC 不是回滚方法，会造成不可恢复的数据丢失。

## 22. 停止和卸载

### 22.1 只停止应用，保留数据库

```bash
kubectl scale deployment/api deployment/web -n xhbx-rag --replicas=0
```

恢复：

```bash
kubectl scale deployment/api deployment/web -n xhbx-rag --replicas=1
```

### 22.2 删除工作负载但保留 PVC

```bash
kubectl delete -f k8s/06-access.yaml --ignore-not-found
kubectl delete -f k8s/05-application.yaml --ignore-not-found
kubectl delete -f k8s/04-index-job.yaml --ignore-not-found
kubectl delete -f k8s/03-infrastructure.yaml --ignore-not-found
```

这种方式保留 Namespace、Secret、ConfigMap 和 PVC。

### 22.3 彻底删除命名空间

高风险命令：

```bash
kubectl delete namespace xhbx-rag
```

该命令会删除命名空间内的工作负载、Secret、ConfigMap 和 PVC。删除 namespace 不一定删除底层 PV，最终行为由 StorageClass 的 reclaimPolicy 决定：

- `Delete`：PVC 删除后底层 PV 和云盘通常也会删除。
- `Retain`：PV 和底层存储保留，需要管理员手工处理。

执行前检查：

```bash
kubectl get pvc -n xhbx-rag
kubectl get pv
kubectl get storageclass -o custom-columns='NAME:.metadata.name,RECLAIM:.reclaimPolicy'
```

移除节点标签：

```bash
kubectl label node <节点名> xhbx-rag/offline-
```

只有确认不再回滚后，才在节点删除明确的本项目镜像；删除后可通过原离线包重新导入恢复：

```bash
sudo ctr -n k8s.io images rm \
  localhost/xhbx-rag-api:offline \
  localhost/xhbx-rag-web:offline \
  localhost/xhbx-rag-data:offline \
  localhost/xhbx-rag-etcd:v3.5.25 \
  localhost/xhbx-rag-minio:RELEASE.2024-12-18T13-15-44Z \
  localhost/xhbx-rag-milvus:v2.6.19
```

## 23. 常见故障排查

### 23.1 `ErrImageNeverPull`

原因通常是镜像没有导入 Pod 所在节点，或导入了错误的 containerd 命名空间。

```bash
kubectl get pod -n xhbx-rag -o wide
kubectl describe pod <Pod名> -n xhbx-rag
sudo ctr -n k8s.io images list | grep 'localhost/xhbx-rag-'
```

确认 Pod 节点就是导入镜像并添加标签的节点。

### 23.2 Pod 一直 `Pending`

查看事件：

```bash
kubectl describe pod <Pod名> -n xhbx-rag
kubectl get nodes -l xhbx-rag/offline=true
```

常见原因：节点未打标签、CPU/内存不足、PVC 未绑定或节点 taint 未配置容忍。当前清单不会自动绕过客户集群 taint。

### 23.3 PVC 一直 `Pending`

```bash
kubectl get pvc -n xhbx-rag
kubectl describe pvc <PVC名> -n xhbx-rag
kubectl get storageclass
```

检查默认 StorageClass、容量配额、访问模式和 provisioner 状态。

### 23.4 Milvus 被 Pod Security 拒绝

Milvus 清单使用：

```yaml
seccompProfile:
  type: Unconfined
```

这与现有 Docker Compose 的 `seccomp:unconfined` 一致。严格 Pod Security Admission 环境可能拒绝该 Pod，需要集群管理员为命名空间或工作负载配置合规例外，不能靠反复重启解决。

### 23.5 索引 Job 失败

```bash
kubectl describe job xhbx-rag-index -n xhbx-rag
kubectl logs job/xhbx-rag-index -n xhbx-rag --all-containers --tail=500
```

重点检查：

- Embedding 地址是否可从 Pod 访问。
- Embedding Key 和模型名是否正确。
- 返回向量维度是否稳定。
- `parsed/` 数据是否通过规范化预检。
- Milvus 是否 Ready。
- Job 的 8Gi 临时磁盘是否足够。

### 23.6 API Ready 但问答失败

```bash
kubectl logs deployment/api -n xhbx-rag --tail=500
kubectl get configmap xhbx-rag-config -n xhbx-rag -o yaml
```

依次检查 Chat、Embedding、Rerank 根地址，确认没有重复拼接最终路径；再检查统一 collection 的 `row_count`。

### 23.7 hostPort 无法访问

```bash
kubectl get pod -l app=web -n xhbx-rag -o wide
kubectl get endpoints web -n xhbx-rag
sudo ss -lntp | grep ':33004 ' || true
curl -fsS http://<节点IP>:33004/api/status
```

确认访问的节点 IP 与 Web Pod 所在节点一致，目标节点没有其他进程占用 `33004`，并检查客户安全组、节点防火墙和集群准入策略。

## 24. 上线前检查清单

- [ ] 目标节点架构与离线包平台一致。
- [ ] `images.tar` SHA-256 校验一致。
- [ ] 六个镜像已导入 Kubernetes 实际容器运行时。
- [ ] 只给已导入镜像的节点添加离线标签。
- [ ] 内网模型地址和模型名已经替换。
- [ ] 模型 Key 与 MinIO 密码已经按客户要求配置。
- [ ] 默认 StorageClass 或显式 `storageClassName` 已确认。
- [ ] etcd、MinIO、Milvus 全部 Ready。
- [ ] 索引 Job 为 `Complete`，日志没有失败或降级误报。
- [ ] `xhbx_knowledge_chunks` 存在且 `row_count > 0`。
- [ ] API 与 Web Pod Ready。
- [ ] `/api/status` 返回 `ok=true`。
- [ ] 真实 `/api/answer` 返回非空答案。
- [ ] Web 所在节点的 `33004` 未被占用，hostPort 或 Ingress 只在批准的内网范围开放。
- [ ] 已保存当前和上一版离线包及 PVC 备份策略。
