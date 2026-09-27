#!/usr/bin/env bash
# 在你自己的电脑上运行：把代码传到服务器并完成安装 / 更新。
#
#   ./deploy/deploy.sh            # 默认 ssh 主机名 ecs（即你平时用的 `ssh ecs`）
#   ./deploy/deploy.sh 其他主机名
#
# 服务器上的数据（data/）和配置（.env）不会被覆盖。
set -euo pipefail

HOST="${1:-ecs}"
REMOTE_DIR="${REMOTE_DIR:-/opt/bili-notes}"
cd "$(dirname "$0")/.."

echo "==> 上传代码到 $HOST:$REMOTE_DIR"
# 用 tar 通过 ssh 传输，不依赖 rsync；COPYFILE_DISABLE 避免 macOS 的 ._ 文件
COPYFILE_DISABLE=1 tar czf - \
  --exclude='./data' --exclude='./.env' --exclude='./.venv' --exclude='./notes' \
  --exclude='__pycache__' --exclude='*.egg-info' --exclude='.pytest_cache' . \
  | ssh "$HOST" "mkdir -p '$REMOTE_DIR' && tar xzf - -C '$REMOTE_DIR'"

echo "==> 在服务器上安装"
ssh -t "$HOST" "bash '$REMOTE_DIR/deploy/remote-setup.sh' '$REMOTE_DIR'"
