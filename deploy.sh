#!/bin/bash
# ============================================================
# deploy.sh — 自动化部署 / 升级 yt2bili
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 1. 执行依赖与环境初始化 (setup.sh) ==="
bash "$SCRIPT_DIR/setup.sh"

echo "=== 2. 初始化 config.yaml (若不存在) ==="
if [ ! -f "$SCRIPT_DIR/config.yaml" ] && [ -f "$SCRIPT_DIR/config.yaml.example" ]; then
  cp "$SCRIPT_DIR/config.yaml.example" "$SCRIPT_DIR/config.yaml"
  echo "已生成默认 config.yaml"
fi

echo "=== 3. 赋予脚本执行权限 ==="
chmod +x "$SCRIPT_DIR/yt2bili.sh" \
         "$SCRIPT_DIR/youtube_downloader.py" \
         "$SCRIPT_DIR/youtube_login.sh" \
         "$SCRIPT_DIR/translate.py" \
         "$SCRIPT_DIR/transcribe.py" \
         "$SCRIPT_DIR/upload.py" \
         "$SCRIPT_DIR/telegram_bot_runner.py" \
         "$SCRIPT_DIR/deploy.sh" \
         "$SCRIPT_DIR/setup.sh"

echo "=== 4. 重启 Web 与 Telegram Bot 服务 ==="
pkill -f "webapp.py 5000" 2>/dev/null || true
pkill -f "telegram_bot_runner.py" 2>/dev/null || true

nohup "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/webapp.py" 5000 > "$SCRIPT_DIR/output/webapp.log" 2>&1 &
nohup "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/telegram_bot_runner.py" > "$SCRIPT_DIR/output/telegram_bot.log" 2>&1 &

echo "=== [✓] deploy.sh 部署完成！Web 端口: 5000 ==="
