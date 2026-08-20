#!/bin/bash
# ============================================================
# youtube_login.sh — 启动/停止 Firefox + noVNC 用于人工登录 Google/YouTube
# 默认仅监听 127.0.0.1:6080，避免暴露到公网
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE_DIR="$SCRIPT_DIR/data/youtube-firefox"
DISPLAY_NUM=":99"
VNC_PORT="5900"
NOVNC_PORT="6080"
PID_DIR="/tmp/yt2bili_vnc"

mkdir -p "$PROFILE_DIR" "$PID_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info(){ echo -e "${GREEN}[✓]${NC} $1"; }
warn(){ echo -e "${YELLOW}[!]${NC} $1"; }
err(){ echo -e "${RED}[✗]${NC} $1"; exit 1; }

is_running(){
  if [ -f "$PID_DIR/novnc.pid" ] && kill -0 "$(cat "$PID_DIR/novnc.pid")" 2>/dev/null; then
    return 0
  fi
  return 1
}

start(){
  if is_running; then
    warn "noVNC 与 Firefox 已经在运行中！"
    status
    return 0
  fi

  info "正在启动虚拟桌面 Xvfb (Display $DISPLAY_NUM)..."
  Xvfb "$DISPLAY_NUM" -screen 0 1280x800x24 > /dev/null 2>&1 &
  echo $! > "$PID_DIR/xvfb.pid"
  sleep 1

  info "正在启动 Firefox (Profile: $PROFILE_DIR)..."
  DISPLAY="$DISPLAY_NUM" firefox-esr --profile "$PROFILE_DIR" "https://accounts.google.com" > /dev/null 2>&1 &
  echo $! > "$PID_DIR/firefox.pid"
  sleep 2

  info "正在启动 x11vnc (仅监听 127.0.0.1:$VNC_PORT)..."
  x11vnc -display "$DISPLAY_NUM" -localhost -nopw -forever -shared > /dev/null 2>&1 &
  echo $! > "$PID_DIR/x11vnc.pid"
  sleep 1

  info "正在启动 noVNC 网页服务 (仅监听 127.0.0.1:$NOVNC_PORT)..."
  websockify --web /usr/share/novnc 127.0.0.1:$NOVNC_PORT 127.0.0.1:$VNC_PORT > /dev/null 2>&1 &
  echo $! > "$PID_DIR/novnc.pid"
  sleep 1

  info "登录服务启动完成！"
  echo ""
  echo "============================================================"
  echo "💡 如何通过 SSH 隧道安全访问 noVNC:"
  echo "在本地电脑终端执行:"
  echo "  ssh -L 6080:127.0.0.1:6080 root@<VPS_IP>"
  echo "然后在本地浏览器打开:"
  echo "  http://127.0.0.1:6080/vnc.html"
  echo "登录完成并关闭浏览器页面后，请执行: ./youtube_login.sh stop"
  echo "============================================================"
}

stop(){
  info "正在停止 noVNC / x11vnc / Firefox / Xvfb..."
  for p in novnc x11vnc firefox xvfb; do
    if [ -f "$PID_DIR/$p.pid" ]; then
      kill "$(cat "$PID_DIR/$p.pid")" 2>/dev/null || true
      rm -f "$PID_DIR/$p.pid"
    fi
  done
  pkill -f "firefox-esr.*youtube-firefox" 2>/dev/null || true
  pkill -f "x11vnc.*$DISPLAY_NUM" 2>/dev/null || true
  pkill -f "Xvfb.*$DISPLAY_NUM" 2>/dev/null || true
  info "所有登录相关服务已停止。"
}

status(){
  echo "=== YouTube 登录环境状态 ==="
  if [ -d "$PROFILE_DIR" ]; then
    echo "Firefox Profile: 存在 ($PROFILE_DIR)"
    if [ -f "$PROFILE_DIR/cookies.sqlite" ]; then
      local count
      count=$(sqlite3 "$PROFILE_DIR/cookies.sqlite" "SELECT count(*) FROM moz_cookies WHERE host LIKE '%youtube%' OR host LIKE '%google%';" 2>/dev/null || echo "0")
      echo "Google/YouTube Cookie 数量: $count"
    fi
  else
    echo "Firefox Profile: 不存在"
  fi

  if is_running; then
    echo "noVNC 状态: 🟢 运行中 (127.0.0.1:$NOVNC_PORT)"
  else
    echo "noVNC 状态: ⚪ 已停止"
  fi
}

test_auth(){
  info "正在测试 Firefox Profile 认证状态..."
  local venv="$SCRIPT_DIR/.venv/bin"
  if [ ! -f "$venv/yt-dlp" ]; then
    venv=""
  fi
  local ytdlp="${venv:+$venv/}yt-dlp"

  "$ytdlp" --cookies-from-browser "firefox:$PROFILE_DIR" --dump-json --skip-download "https://www.youtube.com/watch?v=_onfQRKB1JY" > /tmp/ytdlp_test.json 2>&1
  if grep -q '"title":' /tmp/ytdlp_test.json; then
    info "✅ Firefox 登录态有效！能成功读取视频 metadata。"
  else
    warn "⚠️ Firefox 登录态可能已失效或未登录，请执行 ./youtube_login.sh start 登录。"
  fi
  rm -f /tmp/ytdlp_test.json
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  test) test_auth ;;
  *)
    echo "用法: $0 {start|stop|status|test}"
    exit 1
    ;;
esac
