#!/bin/bash
# ============================================================
# yt2bili 一键部署脚本（幂等，可重复执行）
#
# 用法（VPS 上，root 或可 sudo 用户）：
#   首次: git clone https://github.com/suliaodai00/yt2bili.git /opt/yt2bili
#         cd /opt/yt2bili && bash deploy.sh
#   更新: cd /opt/yt2bili && git pull && bash deploy.sh
#
# 自动完成：系统依赖 → venv → Python 依赖 → Ollama(+并行+模型)
#          → config.yaml → systemd 服务 → 启动验证
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f webapp.py ]; then
  echo "[✗] 未找到 webapp.py，请在项目目录内运行: cd /opt/yt2bili && bash deploy.sh"
  exit 1
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info(){ echo -e "${GREEN}[✓]${NC} $1"; }
step(){ echo -e "${YELLOW}[$(date +%H:%M:%S)]${NC} $1"; }
err(){ echo -e "${RED}[✗]${NC} $1"; exit 1; }

PORT="${PORT:-5000}"
MEM_TOTAL_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)

step "1/6 安装系统依赖 (ffmpeg / python3-venv / curl)..."
if command -v apt-get &>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y -qq ffmpeg python3 python3-venv curl >/dev/null 2>&1 || true
elif command -v apk &>/dev/null; then
  apk add --no-cache ffmpeg python3 py3-pip curl >/dev/null 2>&1 || true
elif command -v yum &>/dev/null; then
  yum install -y ffmpeg python3 python3-pip curl >/dev/null 2>&1 || true
fi
command -v ffmpeg >/dev/null || info "ffmpeg 已存在或安装失败（烧录字幕时才需要）"

step "2/6 创建 Python 虚拟环境并安装依赖..."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip 2>/dev/null || true
.venv/bin/pip install -q yt-dlp biliup pyyaml flask qrcode pillow
info "Python 依赖安装完成"

step "3/6 安装/配置 Ollama..."
if ! command -v ollama &>/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
  sleep 3
fi
if command -v systemctl &>/dev/null; then
  mkdir -p /etc/systemd/system/ollama.service.d
  if [ ! -f /etc/systemd/system/ollama.service.d/override.conf ]; then
    printf '[Service]\nEnvironment=OLLAMA_NUM_PARALLEL=3\n' > /etc/systemd/system/ollama.service.d/override.conf
    systemctl daemon-reload
    systemctl restart ollama 2>/dev/null || true
  fi
fi

# 模型选择：内存 >=14GB 用 qwen2.5:7b，否则 qwen2.5:3b（更快）
if [ "$MEM_TOTAL_MB" -ge 14336 ]; then
  MODEL="qwen2.5:7b"
else
  MODEL="qwen2.5:3b"
fi
info "检测到内存 ${MEM_TOTAL_MB}MB，使用翻译模型 ${MODEL}"
if ! ollama list 2>/dev/null | grep -q "${MODEL%:*}"; then
  info "拉取模型 ${MODEL}（首次需下载，请耐心等待）..."
  ollama pull "$MODEL"
fi

step "4/6 生成 config.yaml（不存在时）..."
if [ ! -f config.yaml ]; then
  cp config.yaml.example config.yaml
  sed -i "s/ollama_model:.*/ollama_model: ${MODEL}/" config.yaml
  info "已生成 config.yaml（翻译模型: ${MODEL}）"
fi

step "5/6 配置系统服务..."
if command -v systemctl &>/dev/null; then
  cat > /etc/systemd/system/yt2bili.service <<EOF
[Unit]
Description=yt2bili monitor panel
After=network.target ollama.service

[Service]
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/.venv/bin/python3 $SCRIPT_DIR/webapp.py $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable yt2bili >/dev/null 2>&1 || true
  systemctl restart yt2bili
  info "systemd 服务 yt2bili 已启动（开机自启已启用）"
else
  info "无 systemd，使用后台进程启动（请自行配置开机自启）"
  pkill -f "webapp.py $PORT" 2>/dev/null || true
  nohup .venv/bin/python3 webapp.py "$PORT" > webapp.log 2>&1 &
fi

step "6/6 健康检查..."
sleep 2
if curl -fsS "http://127.0.0.1:${PORT}/api/system" >/dev/null 2>&1; then
  info "面板已启动: http://127.0.0.1:${PORT}"
  echo ""
  echo "===================="
  echo " 部署完成！"
  echo " 本机访问:  http://127.0.0.1:${PORT}"
  echo " 反向代理:  将域名/Caddy 代理到此端口即可外网访问"
  echo " 查看日志:  journalctl -u yt2bili -f   (systemd)"
  echo " 停止服务:  systemctl stop yt2bili"
  echo "===================="
else
  err "健康检查失败，请查看日志: journalctl -u yt2bili -n 50"
fi
