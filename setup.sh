#!/bin/bash
# ============================================================
# setup.sh — 安装 yt2bili 所需的全部系统依赖与 Python 环境
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

echo "=== 1. 安装系统依赖 (ffmpeg, firefox-esr, xvfb, x11vnc, novnc, websockify) ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq \
  ffmpeg \
  firefox-esr \
  xvfb \
  x11vnc \
  novnc \
  websockify \
  sqlite3 \
  curl \
  python3-venv \
  python3-pip > /dev/null 2>&1 || true

echo "=== 2. 创建 Python 虚拟环境与安装包 ==="
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q \
  yt-dlp \
  bgutil-ytdlp-pot-provider \
  biliup \
  faster-whisper \
  pyyaml \
  flask \
  requests \
  python-telegram-bot

echo "=== 3. 部署 / 启动 PO Token Provider 容器 ==="
if command -v docker >/dev/null 2>&1; then
  docker pull -q brainicism/bgutil-ytdlp-pot-provider:latest 2>/dev/null || true
  docker stop bgutil-pot-provider 2>/dev/null || true
  docker rm bgutil-pot-provider 2>/dev/null || true
  docker run -d \
    --name bgutil-pot-provider \
    --restart unless-stopped \
    -p 127.0.0.1:4416:4416 \
    brainicism/bgutil-ytdlp-pot-provider:latest 2>/dev/null || true
fi

echo "=== 4. 初始化持久化目录 ==="
mkdir -p "$SCRIPT_DIR/data/youtube-firefox" "$SCRIPT_DIR/output/downloads" "$SCRIPT_DIR/output/subtitles" "$SCRIPT_DIR/output/final"

echo "=== [✓] setup.sh 依赖安装完成！==="
