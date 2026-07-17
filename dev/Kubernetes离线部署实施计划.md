# Kubernetes 无镜像仓库离线部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `dev/` 中交付可本地打包、可向无镜像仓库 Kubernetes 节点导入、可按顺序部署并验证的完整离线方案。

**Architecture:** 联网构建机生成固定架构的 API、Web、知识数据和三项基础设施镜像，把它们以 `localhost/` 标签保存为单一 `images.tar`。目标节点把镜像导入 containerd 的 `k8s.io` 命名空间，Kubernetes 通过 `imagePullPolicy: Never` 和 `xhbx-rag/offline=true` 节点标签运行单节点、持久化的 etcd、MinIO、Milvus、API 和 Web；独立 Job 负责首次统一知识库原子重建。

**Tech Stack:** POSIX shell、Docker Buildx、containerd `ctr`、Kubernetes `apps/v1`/`batch/v1`/`networking.k8s.io/v1`、pytest、PyYAML。

## Global Constraints

- 所有交付文件位于 `dev/`；测试位于 `tests/test_k8s_offline_deployment.py`。
- 不引入 Helm、内部镜像仓库或在线拉取步骤。
- 镜像只使用设计文档列出的 `localhost/` 固定标签。
- 所有 Pod 模板设置 `imagePullPolicy: Never` 和 `nodeSelector: {xhbx-rag/offline: "true"}`。
- API 始终为单副本且使用 `Recreate` 更新策略。
- 首次索引只写统一 collection `xhbx_knowledge_chunks`。
- 不修改现有 Docker Compose 离线部署文件。
- 不把真实模型凭据写入仓库。

---

### Task 1: 固定离线交付契约

**Files:**
- Create: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: `dev/` 设计文档中的文件名、镜像名和部署顺序。
- Produces: 后续打包脚本与 Kubernetes 清单必须满足的自动化契约。

- [ ] **Step 1: 编写失败测试**

测试必须包含以下具体断言：

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

def test_k8s_offline_delivery_files_exist() -> None:
    missing = [path for path in EXPECTED_FILES if not (ROOT / path).is_file()]
    assert missing == []
```

同一测试文件还要用 `yaml.safe_load_all` 解析所有清单，遍历 StatefulSet、Deployment 和 Job 的 PodSpec，断言 `nodeSelector`、`imagePullPolicy` 和 `localhost/` 镜像前缀；静态检查索引 Job、单副本 API、`30088` NodePort、打包脚本和导入脚本。

- [ ] **Step 2: 运行测试确认正确失败**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: FAIL，缺失文件列表从 `dev/Dockerfile.data` 开始，而不是测试语法或导入错误。

- [ ] **Step 3: 提交测试契约**

```bash
git add tests/test_k8s_offline_deployment.py
git commit -m "test: define kubernetes offline deployment contract"
```

### Task 2: 实现本地打包和节点镜像导入

**Files:**
- Create: `dev/Dockerfile.data`
- Create: `dev/package_k8s_offline.sh`
- Create: `dev/import_k8s_images.sh`
- Test: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: 仓库根目录的 `Dockerfile.api`、`web/Dockerfile`、`parsed/` 和 `dev/k8s/`。
- Produces: `dist/xhbx-rag-k8s-offline-<arch>.tar.gz`，内含 `images.tar`、校验文件、清单、脚本和文档。

- [ ] **Step 1: 为脚本行为补充失败测试**

```python
def test_package_script_rejects_unknown_platform_without_docker() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "dev/package_k8s_offline.sh"), "invalid"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "amd|arm" in result.stderr

def test_import_script_rejects_unknown_runtime() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "dev/import_k8s_images.sh"), "unknown", "images.tar"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "containerd|docker|crio" in result.stderr
```

- [ ] **Step 2: 运行脚本测试确认失败原因是文件缺失**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: FAIL，两个脚本路径不存在。

- [ ] **Step 3: 实现数据镜像**

`dev/Dockerfile.data` 使用以下完整结构：

```dockerfile
ARG API_IMAGE=localhost/xhbx-rag-api:offline
FROM ${API_IMAGE}
COPY parsed /seed/parsed
```

打包脚本把 `parsed/` 复制到 `mktemp -d` 创建的独立构建上下文，避免根目录 `.dockerignore` 排除 `parsed/`。

- [ ] **Step 4: 实现打包脚本**

`dev/package_k8s_offline.sh` 必须：

1. 只接受 `amd|arm`，分别映射到 `linux/amd64|linux/arm64`。
2. 用 Buildx 构建 API、Web、数据镜像。
3. 按目标架构拉取并重新标记固定版本 etcd、MinIO、Milvus。
4. 显式列出六个镜像执行 `docker save`，禁止依赖未加引号的字符串拆分。
5. 生成 `images.sha256` 和 `package-manifest.txt`。
6. 复制 `dev/k8s`、导入脚本和部署文档。
7. 输出 `dist/xhbx-rag-k8s-offline-<arch>.tar.gz`。

- [ ] **Step 5: 实现导入脚本**

`dev/import_k8s_images.sh` 接口固定为：

```text
sh import_k8s_images.sh containerd images.tar
sh import_k8s_images.sh docker images.tar
sh import_k8s_images.sh crio images.tar
```

containerd 必须执行 `ctr -n k8s.io images import`；Docker 使用 `docker load -i`；CRI-O 使用 `podman load -i`。脚本先校验镜像文件存在，错误运行时返回退出码 2。

- [ ] **Step 6: 运行脚本测试和语法检查**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: 打包/导入脚本相关测试 PASS，清单和文档相关测试仍 FAIL。

Run: `sh -n dev/package_k8s_offline.sh && sh -n dev/import_k8s_images.sh`

Expected: exit 0，无输出。

- [ ] **Step 7: 提交脚本**

```bash
git add dev/Dockerfile.data dev/package_k8s_offline.sh dev/import_k8s_images.sh tests/test_k8s_offline_deployment.py
git commit -m "feat: package kubernetes images for offline nodes"
```

### Task 3: 实现 Kubernetes 基础设施与应用清单

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
- Produces: 按基础设施、索引、应用、访问顺序可独立 apply/wait 的 Kubernetes 资源。

- [ ] **Step 1: 补充资源语义失败测试**

测试必须断言：

```python
assert resources[("Namespace", "xhbx-rag")]
assert resources[("ConfigMap", "xhbx-rag-config")]["data"]["MILVUS_COLLECTION"] == "xhbx_knowledge_chunks"
assert resources[("Deployment", "api")]["spec"]["replicas"] == 1
assert resources[("Deployment", "api")]["spec"]["strategy"]["type"] == "Recreate"
assert resources[("Service", "web-nodeport")]["spec"]["ports"][0]["nodePort"] == 30088
```

索引 Job 测试还必须确认 `xhbx-rag-data:offline` 是 init container，主容器命令同时包含 `normalize-knowledge`、`index-dir`、`--mode rebuild` 和 `xhbx_knowledge_chunks`。

- [ ] **Step 2: 运行测试确认资源缺失**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: FAIL，资源键不存在或清单文件缺失。

- [ ] **Step 3: 创建 Namespace、配置、Secret 和 PVC**

使用命名空间 `xhbx-rag`。ConfigMap 写入内网模型示例地址、模型名、统一 Milvus 地址和 Web 参数；Secret 的模型 Key 使用 `not-required`，MinIO 密码使用明确的部署前替换值。PVC 使用默认 StorageClass 和设计文档中的容量。

- [ ] **Step 4: 创建 etcd、MinIO、Milvus**

三个组件均为单副本 StatefulSet 和 ClusterIP Service，挂载各自 PVC，复制现有 `docker-compose.offline.yml` 的固定镜像版本、启动参数和健康检查口径。Milvus 使用 `seccompProfile: Unconfined`。

- [ ] **Step 5: 创建统一知识库索引 Job**

init container 从 `/seed/parsed` 复制到共享卷，API 主容器依次执行：

```sh
xhbx-rag normalize-knowledge --input-dir /work/parsed --out /work/normalized
xhbx-rag index-dir --chunks-dir /work/normalized --collection-name xhbx_knowledge_chunks --mode rebuild --batch-size 64
```

- [ ] **Step 6: 创建 API、Web 和访问资源**

API 使用一个副本、`Recreate`、两个 PVC、`/api/status` 探针和 ClusterIP Service。Web 使用 Nginx 镜像、ClusterIP Service；访问文件同时提供 `30088` NodePort 与可编辑主机名的 Ingress。

- [ ] **Step 7: 运行清单测试**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: 所有脚本和清单测试 PASS，仅部署文档测试可能仍 FAIL。

- [ ] **Step 8: 提交 Kubernetes 清单**

```bash
git add dev/k8s tests/test_k8s_offline_deployment.py
git commit -m "feat: add registryless kubernetes manifests"
```

### Task 4: 编写可执行中文部署文档

**Files:**
- Create: `dev/Kubernetes离线部署文档.md`
- Test: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: Task 2 的脚本接口和 Task 3 的资源文件名。
- Produces: 从架构确认到卸载风险提示的完整操作手册。

- [ ] **Step 1: 编写文档覆盖失败测试**

测试逐项检查以下命令和风险提示存在：

```text
sh dev/package_k8s_offline.sh amd
sh dev/package_k8s_offline.sh arm
sh import_k8s_images.sh containerd images.tar
kubectl label node
kubectl wait --for=condition=ready pod
kubectl wait --for=condition=complete job/xhbx-rag-index
http://<节点IP>:30088
kubectl logs job/xhbx-rag-index
kubectl rollout undo deployment/api
kubectl delete namespace xhbx-rag
删除 namespace 不一定删除底层 PV
```

- [ ] **Step 2: 运行测试确认文档缺失**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: FAIL，`dev/Kubernetes离线部署文档.md` 不存在。

- [ ] **Step 3: 编写部署文档**

文档按以下顺序组织：部署结论、架构与限制、前置检查、本地打包、离线传输、运行时识别、节点导入、配置修改、PVC、基础设施启动、索引 Job、应用启动、NodePort/Ingress、验收、日志、升级、重新入库、备份恢复、回滚、卸载和故障排查。

所有命令使用离线包内的实际相对路径；明确只有导入过镜像并打标签的节点才能运行；明确不包含模型服务、Docker/containerd 安装包和 `data/` 原始资料。

- [ ] **Step 4: 运行文档测试**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: PASS。

- [ ] **Step 5: 提交文档**

```bash
git add dev/Kubernetes离线部署文档.md tests/test_k8s_offline_deployment.py
git commit -m "docs: add kubernetes offline deployment guide"
```

### Task 5: 全量验证与交付审查

**Files:**
- Verify: `dev/**`
- Verify: `tests/test_k8s_offline_deployment.py`

**Interfaces:**
- Consumes: Tasks 1-4 的全部产物。
- Produces: 可交付的验证证据和明确的未执行边界。

- [ ] **Step 1: 运行专项测试**

Run: `uv run pytest tests/test_k8s_offline_deployment.py -q`

Expected: 全部 PASS。

- [ ] **Step 2: 运行相关离线部署回归测试**

Run: `uv run pytest tests/test_k8s_offline_deployment.py tests/test_web_offline_deployment.py tests/test_docker_deployment.py -q`

Expected: 全部 PASS；如果既有测试暴露当前主分支已经存在的文档漂移，必须单独报告，不用修改不在本任务范围内的 Compose 方案。

- [ ] **Step 3: 运行 ShellCheck 和 shell 语法检查**

Run: `sh -n dev/package_k8s_offline.sh && sh -n dev/import_k8s_images.sh`

Expected: exit 0。

如果本机存在 ShellCheck：

Run: `shellcheck dev/package_k8s_offline.sh dev/import_k8s_images.sh`

Expected: exit 0。

- [ ] **Step 4: 校验 YAML 和敏感信息**

Run: `uv run python -c 'import pathlib,yaml; [list(yaml.safe_load_all(p.read_text())) for p in pathlib.Path("dev/k8s").glob("*.yaml")]'`

Expected: exit 0。

Run: `rg -n 'sk-[A-Za-z0-9]|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY' dev`

Expected: exit 1，无匹配。

- [ ] **Step 5: 审查差异和占位表达**

Run: `git diff --check && git status --short && git diff --stat main...HEAD`

Expected: `git diff --check` exit 0；状态中只包含本任务文件；差异范围与本计划一致。

- [ ] **Step 6: 记录无法在当前机器完成的真实环境验证**

完整镜像构建、跨架构镜像导入、PVC 动态供给、Pod 调度和真实问答必须在具备 Docker daemon 与目标 Kubernetes 集群的环境执行。交付时明确这些步骤未被本地静态测试替代。
