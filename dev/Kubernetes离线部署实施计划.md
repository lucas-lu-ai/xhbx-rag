# Kubernetes 无镜像仓库离线部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `dev/` 中交付可本地打包、可向无镜像仓库 Kubernetes 节点导入、可按顺序部署并验证的完整离线方案。

**Architecture:** 联网构建机生成固定架构的 API、Web、知识数据和基础设施镜像，把它们以 `localhost/` 标签保存为单一 `images.tar`。目标节点把镜像导入 containerd 的 `k8s.io` 命名空间，Kubernetes 通过 `imagePullPolicy: Never` 和 `xhbx-rag/offline=true` 节点标签运行单节点持久化服务；独立 Job 负责首次统一知识库原子重建。

**Tech Stack:** POSIX shell、Docker Buildx、containerd `ctr`、Kubernetes `apps/v1`/`batch/v1`/`networking.k8s.io/v1`、pytest、PyYAML。

## Global Constraints

- 所有交付文件位于 `dev/`，自动化检查位于 `tests/test_k8s_offline_deployment.py`。
- 不引入 Helm、内部镜像仓库或在线拉取步骤。
- 镜像使用设计文档列出的 `localhost/` 固定标签。
- 所有 Pod 模板设置 `imagePullPolicy: Never` 和 `nodeSelector: {xhbx-rag/offline: "true"}`。
- API 始终为单副本且使用 `Recreate` 更新策略。
- 首次索引只写统一 collection `xhbx_knowledge_chunks`。
- 不修改现有 Docker Compose 离线部署文件，不写入真实模型凭据。

---

### Task 1: 固定离线交付契约

**Files:**
- Create: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: 设计文档中的文件名、镜像名和部署顺序。
- Produces: 后续脚本、清单与文档必须满足的静态和行为契约。

- [ ] **Step 1: 编写失败测试**

测试先断言以下文件均存在：

```python
EXPECTED_FILES = (
    "dev/Dockerfile.data",
    "dev/package_k8s_offline.sh",
    "dev/import_k8s_images.sh",
    "dev/Kubernetes离线部署文档.md",
    "dev/k8s/00-namespace.yaml",
    "dev/k8s/01-config.yaml",
    "dev/k8s/02-storage.yaml",
    "dev/k8s/03-infrastructure.yaml",
    "dev/k8s/04-index-job.yaml",
    "dev/k8s/05-application.yaml",
    "dev/k8s/06-access.yaml",
)
```

同一测试文件使用 `yaml.safe_load_all` 解析清单，遍历 StatefulSet、Deployment 和 Job 的 PodSpec，断言 `nodeSelector`、`imagePullPolicy` 和 `localhost/` 镜像前缀；同时检查索引 Job、单副本 API、Web `hostPort: 33004`、打包脚本和导入脚本。

- [ ] **Step 2: 运行测试确认正确失败**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: FAIL，原因是新交付文件尚不存在。

- [ ] **Step 3: 提交测试契约**

```bash
git add tests/test_k8s_offline_deployment.py
git commit -m "test: define kubernetes offline deployment contract"
```

### Task 2: 实现本地打包和节点导入

**Files:**
- Create: `dev/Dockerfile.data`
- Create: `dev/package_k8s_offline.sh`
- Create: `dev/import_k8s_images.sh`
- Test: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: `Dockerfile.api`、`web/Dockerfile`、`parsed/` 和 `dev/k8s/`。
- Produces: `dist/xhbx-rag-k8s-offline-<arch>.tar.gz`。

- [ ] **Step 1: 补充失败测试**

```python
def test_package_script_rejects_unknown_platform_without_docker() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "dev/package_k8s_offline.sh"), "invalid"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "amd|arm" in result.stderr

def test_import_script_rejects_unknown_runtime() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "dev/import_k8s_images.sh"), "unknown", "images.tar"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "containerd|docker|crio" in result.stderr
```

- [ ] **Step 2: 运行测试并确认因脚本缺失而失败**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

- [ ] **Step 3: 实现数据镜像**

`dev/Dockerfile.data` 使用固定多架构基础镜像，避免 Buildx 尝试从 `localhost` 拉取刚构建的 API 镜像：

```dockerfile
FROM busybox:1.36.1
COPY parsed /seed/parsed
```

打包脚本把 `parsed/` 复制到 `mktemp -d` 创建的独立构建上下文，避免根目录 `.dockerignore` 排除 `parsed/`。

- [ ] **Step 4: 实现打包脚本**

脚本只接受 `amd|arm`；用 Buildx 构建 API、Web、数据镜像；按目标平台拉取并重标记固定版本 etcd、MinIO、Milvus；显式列出六个镜像执行 `docker save`；生成 SHA-256、清单和最终压缩包。

- [ ] **Step 5: 实现导入脚本**

接口固定为：

```text
sh import_k8s_images.sh containerd images.tar
sh import_k8s_images.sh docker images.tar
sh import_k8s_images.sh crio images.tar
```

containerd 使用 `ctr -n k8s.io images import`，Docker 使用 `docker load -i`，CRI-O 使用 `podman load -i`。

- [ ] **Step 6: 验证并提交**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Run: `sh -n dev/package_k8s_offline.sh && sh -n dev/import_k8s_images.sh`

```bash
git add dev/Dockerfile.data dev/package_k8s_offline.sh dev/import_k8s_images.sh tests/test_k8s_offline_deployment.py
git commit -m "feat: package kubernetes images for offline nodes"
```

### Task 3: 实现 Kubernetes 清单

**Files:**
- Create: `dev/k8s/00-namespace.yaml`
- Create: `dev/k8s/01-config.yaml`
- Create: `dev/k8s/02-storage.yaml`
- Create: `dev/k8s/03-infrastructure.yaml`
- Create: `dev/k8s/04-index-job.yaml`
- Create: `dev/k8s/05-application.yaml`
- Create: `dev/k8s/06-access.yaml`
- Test: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: Task 2 的六个 `localhost/` 镜像标签。
- Produces: 可按基础设施、索引、应用、访问顺序执行的资源。

- [ ] **Step 1: 补充资源语义失败测试**

```python
assert resources[("Namespace", "xhbx-rag")]
assert resources[("ConfigMap", "xhbx-rag-config")]["data"]["MILVUS_COLLECTION"] == "xhbx_knowledge_chunks"
assert resources[("Deployment", "api")]["spec"]["replicas"] == 1
assert resources[("Deployment", "api")]["spec"]["strategy"]["type"] == "Recreate"
web_container = pod_spec(resources[("Deployment", "web")])["containers"][0]
assert web_container["ports"][0]["hostPort"] == 33004
assert ("Service", "web-nodeport") not in resources
```

索引 Job 还要确认数据镜像位于 init container，主容器命令包含 `normalize-knowledge`、`index-dir`、`--mode rebuild` 和 `xhbx_knowledge_chunks`。

- [ ] **Step 2: 运行测试确认资源缺失**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

- [ ] **Step 3: 创建 Namespace、配置、Secret 和 PVC**

ConfigMap 写入内网模型示例地址、模型名、统一 Milvus 地址和 Web 参数；Secret 的模型 Key 使用 `not-required`，MinIO 密码使用明确的部署前替换值。PVC 使用默认 StorageClass 和设计容量。

- [ ] **Step 4: 创建基础设施、索引和应用**

etcd、MinIO、Milvus 使用单副本 StatefulSet 与 ClusterIP Service；Milvus 设置 `seccompProfile: Unconfined`。索引 Job 先复制 `/seed/parsed`，再执行：

```sh
xhbx-rag normalize-knowledge --input-dir /work/parsed --out /work/normalized
xhbx-rag index-dir --chunks-dir /work/normalized --collection-name xhbx_knowledge_chunks --mode rebuild --batch-size 64
```

API 使用一个副本、`Recreate`、两个 PVC、`/api/status` 探针和 ClusterIP Service；Web 提供 ClusterIP、`hostPort: 33004` 与可编辑主机名的 Ingress，不再创建 NodePort Service。

- [ ] **Step 5: 验证并提交**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

```bash
git add dev/k8s tests/test_k8s_offline_deployment.py
git commit -m "feat: add registryless kubernetes manifests"
```

### Task 4: 编写中文部署文档

**Files:**
- Create: `dev/Kubernetes离线部署文档.md`
- Test: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: 打包/导入脚本接口和全部资源文件名。
- Produces: 从架构确认到卸载风险提示的完整操作手册。

- [ ] **Step 1: 编写文档覆盖失败测试**

检查文档包含打包、containerd 导入、节点标签、资源 apply/wait、`33004` 访问、hostPort 占用检查、日志、升级、重新入库、备份、回滚和 namespace/PV 删除风险。

- [ ] **Step 2: 运行测试确认文档缺失**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

- [ ] **Step 3: 编写部署文档**

所有命令使用离线包内实际相对路径；明确只有导入过镜像并打标签的节点才能运行；明确不包含模型服务、容器运行时安装包和 `data/` 原始资料。

- [ ] **Step 4: 验证并提交**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

```bash
git add dev/Kubernetes离线部署文档.md tests/test_k8s_offline_deployment.py
git commit -m "docs: add kubernetes offline deployment guide"
```

### Task 5: 全量验证

**Files:**
- Verify: `dev/**`
- Verify: `tests/test_k8s_offline_deployment.py`

- [ ] **Step 1: 运行专项和相关回归测试**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Run: `uv run pytest tests/test_k8s_offline_deployment.py tests/test_web_offline_deployment.py tests/test_docker_deployment.py -q`

- [ ] **Step 2: 检查 shell、YAML 和敏感信息**

Run: `sh -n dev/package_k8s_offline.sh && sh -n dev/import_k8s_images.sh`

Run: `uv run python -c 'import pathlib,yaml; [list(yaml.safe_load_all(p.read_text())) for p in pathlib.Path("dev/k8s").glob("*.yaml")]'`

Run: `rg -n 'sk-[A-Za-z0-9]|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY' dev`

- [ ] **Step 3: 审查差异并记录真实环境边界**

Run: `git diff --check && git status --short && git diff --stat main...HEAD`

完整镜像构建、跨架构镜像导入、PVC 动态供给、Pod 调度和真实问答必须在具备 Docker daemon 与目标 Kubernetes 集群的环境执行，不能用本地静态测试替代。
