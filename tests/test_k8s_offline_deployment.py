from __future__ import annotations

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
K8S_FILES = (
    "dev/k8s/00-namespace.yaml",
    "dev/k8s/01-config.yaml",
    "dev/k8s/02-storage.yaml",
    "dev/k8s/03-infrastructure.yaml",
    "dev/k8s/04-index-job.yaml",
    "dev/k8s/05-application.yaml",
    "dev/k8s/06-access.yaml",
)
EXPECTED_FILES = (
    "dev/Dockerfile.data",
    "dev/package_k8s_offline.sh",
    "dev/import_k8s_images.sh",
    "dev/Kubernetes离线部署文档.md",
    *K8S_FILES,
)


def read_repo_file(path: str) -> str:
    target = ROOT / path
    assert target.is_file(), f"交付文件不存在: {path}"
    return target.read_text(encoding="utf-8")


def load_resources() -> dict[tuple[str, str], dict]:
    resources: dict[tuple[str, str], dict] = {}
    for path in K8S_FILES:
        content = read_repo_file(path)
        for resource in yaml.safe_load_all(content):
            if not resource:
                continue
            key = (resource["kind"], resource["metadata"]["name"])
            assert key not in resources, f"Kubernetes 资源重名: {key}"
            resources[key] = resource
    return resources


def pod_spec(resource: dict) -> dict:
    return resource["spec"]["template"]["spec"]


def test_k8s_offline_delivery_files_exist() -> None:
    missing = [path for path in EXPECTED_FILES if not (ROOT / path).is_file()]

    assert missing == []


def test_data_dockerfile_uses_fixed_base_and_embeds_parsed_data() -> None:
    dockerfile = read_repo_file("dev/Dockerfile.data")

    assert "FROM busybox:1.36.1" in dockerfile
    assert "API_IMAGE" not in dockerfile
    assert "COPY parsed /seed/parsed" in dockerfile


def test_package_script_builds_arch_specific_complete_bundle() -> None:
    script = read_repo_file("dev/package_k8s_offline.sh")

    assert 'amd) PLATFORM_SUFFIX="amd64"; DOCKER_PLATFORM="linux/amd64"' in script
    assert 'arm) PLATFORM_SUFFIX="arm64"; DOCKER_PLATFORM="linux/arm64"' in script
    assert "docker buildx build" in script
    assert 'cp -R parsed/. "$DATA_CONTEXT/parsed/"' in script
    assert "dev/Dockerfile.data" in script
    for image in (
        "localhost/xhbx-rag-api:offline",
        "localhost/xhbx-rag-web:offline",
        "localhost/xhbx-rag-data:offline",
        "localhost/xhbx-rag-etcd:v3.5.25",
        "localhost/xhbx-rag-minio:RELEASE.2024-12-18T13-15-44Z",
        "localhost/xhbx-rag-milvus:v2.6.19",
    ):
        assert image in script
    assert 'docker save -o "$PACKAGE_DIR/images.tar" \\' in script
    assert "images.sha256" in script
    assert "package-manifest.txt" in script
    assert 'cp -R dev/k8s "$PACKAGE_DIR/k8s"' in script
    assert "Kubernetes离线部署文档.md" in script
    assert "data/" not in script


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


def test_import_script_supports_kubernetes_container_runtimes() -> None:
    script = read_repo_file("dev/import_k8s_images.sh")

    assert 'ctr -n k8s.io images import "$IMAGE_TAR"' in script
    assert 'docker load -i "$IMAGE_TAR"' in script
    assert 'podman load -i "$IMAGE_TAR"' in script
    assert "containerd|docker|crio" in script


def test_import_script_rejects_unknown_runtime() -> None:
    result = subprocess.run(
        [
            "sh",
            str(ROOT / "dev/import_k8s_images.sh"),
            "unknown",
            "images.tar",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "containerd|docker|crio" in result.stderr


def test_all_kubernetes_documents_parse_and_have_expected_resources() -> None:
    resources = load_resources()

    for key in (
        ("Namespace", "xhbx-rag"),
        ("ConfigMap", "xhbx-rag-config"),
        ("Secret", "xhbx-rag-secrets"),
        ("StatefulSet", "etcd"),
        ("StatefulSet", "minio"),
        ("StatefulSet", "standalone"),
        ("Job", "xhbx-rag-index"),
        ("Deployment", "api"),
        ("Deployment", "web"),
        ("Service", "web-nodeport"),
        ("Ingress", "xhbx-rag"),
    ):
        assert key in resources


def test_all_pods_are_pinned_to_offline_nodes_and_never_pull() -> None:
    resources = load_resources()
    workloads = {
        key: resource
        for key, resource in resources.items()
        if key[0] in {"StatefulSet", "Deployment", "Job"}
    }

    assert workloads
    for key, resource in workloads.items():
        spec = pod_spec(resource)
        assert spec["nodeSelector"] == {"xhbx-rag/offline": "true"}, key
        containers = spec.get("initContainers", []) + spec["containers"]
        assert containers, key
        for container in containers:
            assert container["image"].startswith("localhost/"), (key, container)
            assert container["imagePullPolicy"] == "Never", (key, container)


def test_config_uses_unified_collection_and_no_real_secrets() -> None:
    resources = load_resources()
    config = resources[("ConfigMap", "xhbx-rag-config")]["data"]
    secret = resources[("Secret", "xhbx-rag-secrets")]["stringData"]

    assert config["MILVUS_MODE"] == "docker"
    assert config["MILVUS_URI"] == "http://standalone:19530"
    assert config["MILVUS_COLLECTION"] == "xhbx_knowledge_chunks"
    assert config["WEB_BATCH_CONCURRENCY"] == "3"
    assert secret["API_KEY"] == "not-required"
    assert secret["EMBEDDING_API_KEY"] == "not-required"
    assert secret["RERANK_API_KEY"] == "not-required"
    assert not any(value.startswith("sk-") for value in secret.values())


def test_storage_has_persistent_claims_for_stateful_services_and_api() -> None:
    resources = load_resources()
    expected_sizes = {
        "etcd-data": "10Gi",
        "minio-data": "50Gi",
        "milvus-data": "50Gi",
        "api-data": "100Gi",
        "api-local": "10Gi",
    }

    for name, size in expected_sizes.items():
        claim = resources[("PersistentVolumeClaim", name)]
        assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert claim["spec"]["resources"]["requests"]["storage"] == size


def test_infrastructure_is_internal_and_milvus_keeps_required_seccomp() -> None:
    resources = load_resources()

    for name in ("etcd", "minio", "standalone"):
        service = resources[("Service", name)]
        assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    milvus = resources[("StatefulSet", "standalone")]
    assert pod_spec(milvus)["securityContext"]["seccompProfile"]["type"] == "Unconfined"


def test_index_job_seeds_data_then_rebuilds_unified_collection() -> None:
    resources = load_resources()
    job = resources[("Job", "xhbx-rag-index")]
    spec = pod_spec(job)

    assert spec["restartPolicy"] == "Never"
    assert spec["initContainers"][0]["image"] == "localhost/xhbx-rag-data:offline"
    command = "\n".join(spec["containers"][0]["command"] + spec["containers"][0]["args"])
    for text in (
        "normalize-knowledge",
        "index-dir",
        "--mode rebuild",
        "xhbx_knowledge_chunks",
    ):
        assert text in command


def test_api_is_single_replica_recreate_and_web_proxies_to_it() -> None:
    resources = load_resources()
    api = resources[("Deployment", "api")]
    web = resources[("Deployment", "web")]

    assert api["spec"]["replicas"] == 1
    assert api["spec"]["strategy"]["type"] == "Recreate"
    api_container = pod_spec(api)["containers"][0]
    assert api_container["readinessProbe"]["httpGet"]["path"] == "/api/status"
    assert api_container["livenessProbe"]["httpGet"]["path"] == "/api/status"
    assert web["spec"]["replicas"] == 1
    assert resources[("Service", "api")]["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert resources[("Service", "web")]["spec"].get("type", "ClusterIP") == "ClusterIP"


def test_access_offers_nodeport_and_ingress_without_exposing_api() -> None:
    resources = load_resources()
    nodeport = resources[("Service", "web-nodeport")]
    ingress = resources[("Ingress", "xhbx-rag")]

    assert nodeport["spec"]["type"] == "NodePort"
    assert nodeport["spec"]["ports"][0]["nodePort"] == 30088
    assert ingress["spec"]["rules"][0]["host"] == "xhbx-rag.internal.example"
    assert ("Service", "api-nodeport") not in resources
    assert ("Ingress", "api") not in resources


def test_deployment_doc_covers_offline_delivery_and_operations() -> None:
    docs = read_repo_file("dev/Kubernetes离线部署文档.md")

    for text in (
        "sh dev/package_k8s_offline.sh amd",
        "sh dev/package_k8s_offline.sh arm",
        "sh import_k8s_images.sh containerd images.tar",
        "kubectl label node",
        "xhbx-rag/offline=true",
        "kubectl wait --for=condition=complete job/xhbx-rag-index",
        "http://<节点IP>:30088",
        "kubectl logs job/xhbx-rag-index",
        "kubectl rollout undo deployment/api",
        "kubectl delete namespace xhbx-rag",
        "删除 namespace 不一定删除底层 PV",
        "imagePullPolicy: Never",
        "不包含模型权重",
        "不包含 `data/`",
    ):
        assert text in docs
    assert "sk-" not in docs
