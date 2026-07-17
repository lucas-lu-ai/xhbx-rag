#!/bin/sh
set -eu

RUNTIME="${1:-}"
IMAGE_TAR="${2:-images.tar}"

case "$RUNTIME" in
  containerd|docker|crio) ;;
  *)
    echo "用法: $0 containerd|docker|crio [images.tar]" >&2
    exit 2
    ;;
esac

if [ ! -f "$IMAGE_TAR" ]; then
  echo "镜像包不存在: $IMAGE_TAR" >&2
  exit 1
fi

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
    return
  fi
  echo "当前用户不是 root，且未找到 sudo" >&2
  return 126
}

case "$RUNTIME" in
  containerd)
    if ! command -v ctr >/dev/null 2>&1; then
      echo "未找到 ctr" >&2
      exit 127
    fi
    run_privileged ctr -n k8s.io images import "$IMAGE_TAR"
    run_privileged ctr -n k8s.io images list | grep 'localhost/xhbx-rag-'
    ;;
  docker)
    if ! command -v docker >/dev/null 2>&1; then
      echo "未找到 docker" >&2
      exit 127
    fi
    run_privileged docker load -i "$IMAGE_TAR"
    run_privileged docker images --format '{{.Repository}}:{{.Tag}}' \
      | grep 'localhost/xhbx-rag-'
    ;;
  crio)
    if ! command -v podman >/dev/null 2>&1; then
      echo "未找到 podman" >&2
      exit 127
    fi
    run_privileged podman load -i "$IMAGE_TAR"
    run_privileged podman images --format '{{.Repository}}:{{.Tag}}' \
      | grep 'localhost/xhbx-rag-'
    ;;
esac

echo "镜像导入完成。下一步给本节点添加 xhbx-rag/offline=true 标签。"
