#!/usr/bin/env bash
# 在服务器上运行（deploy.sh 会自动调用）：安装 Python 依赖、注册 systemd 服务并启动。
set -euo pipefail

DIR="${1:-/opt/bili-notes}"
PORT="${PORT:-8765}"
PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"  # 阿里云内网访问很快
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
cd "$DIR"

# ---- 1. 找一个 >= 3.10 的 Python ---------------------------------------------
find_python() {
  for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
      echo "$py"; return 0
    fi
  done
  return 1
}
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  echo "==> 没有找到 Python 3.10+，尝试安装"
  if command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3.11 python3.11-pip || $SUDO dnf install -y python3.12 python3.12-pip
  elif command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update && $SUDO apt-get install -y python3 python3-venv
  fi
  PY="$(find_python || true)"
  [ -n "$PY" ] || { echo "❌ 请先手动安装 Python 3.10 或更高版本"; exit 1; }
fi
if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get install -y python3-venv
fi
echo "==> 使用 $("$PY" --version)"

# ---- 2. 虚拟环境与依赖 --------------------------------------------------------
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q -i "$PIP_INDEX" --upgrade pip
EXTRAS="${EXTRAS:-asr}"   # 不需要语音识别可以用 EXTRAS= 跳过，省约 500MB
.venv/bin/pip install -q -i "$PIP_INDEX" -e ".${EXTRAS:+[$EXTRAS]}"

# ---- 3. 配置文件 ---------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo
  echo "⚠️  已生成配置文件 $DIR/.env，请先填写后再运行一次部署："
  echo "    ssh ${SSH_HOST:-ecs}  然后  vi $DIR/.env"
  echo "    至少需要：DEEPSEEK_API_KEY、APP_PASSWORD、BILI_COOKIE、BILI_FAV_FOLDER"
  exit 0
fi
grep -q '^APP_PASSWORD=..*' .env || { echo "❌ .env 里必须设置 APP_PASSWORD"; exit 1; }

# ---- 4. systemd 服务 -----------------------------------------------------------
$SUDO tee /etc/systemd/system/bili-notes.service >/dev/null <<UNIT
[Unit]
Description=bili-notes B站视频知识库
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/bili-notes serve --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
$SUDO systemctl daemon-reload
$SUDO systemctl enable bili-notes >/dev/null 2>&1
$SUDO systemctl restart bili-notes

# ---- 5. 检查 -------------------------------------------------------------------
for _ in $(seq 1 20); do
  curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
if ! curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "❌ 服务没有正常启动，最近日志："
  $SUDO journalctl -u bili-notes -n 30 --no-pager
  exit 1
fi
IP="$(curl -fs -m 2 http://100.100.100.200/latest/meta-data/eipv4 2>/dev/null \
   || curl -fs -m 2 http://100.100.100.200/latest/meta-data/public-ipv4 2>/dev/null || echo '服务器公网IP')"
echo
echo "✅ 部署完成：http://$IP:$PORT"
echo "   如果手机打不开，请在阿里云控制台 → 安全组 → 入方向放行 TCP $PORT"
echo "   查看日志：journalctl -u bili-notes -f"
