#!/bin/bash
# ============================================================
# install_snell_proxy.sh — 在 VPS 上安装 mihomo 并配置 snell 节点为本地代理
# 生成 /etc/mihomo/config.yaml + systemd 服务，监听 127.0.0.1:1080
#
# 用法:
#   bash install_snell_proxy.sh <服务器> <端口> <psk> [版本]
#   例: bash install_snell_proxy.sh 189.24.112.39 2345 6XInGrtO4ZE0juez 5
#
# 完成后在 config.yaml 填:  proxy: "http://127.0.0.1:1080"
# ============================================================
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "用法: bash install_snell_proxy.sh <服务器> <端口> <psk> [版本]"
  exit 1
fi

SERVER="$1"
PORT="$2"
PSK="$3"
VERSION="${4:-5}"
LISTEN_PORT="${LISTEN_PORT:-1080}"

info(){ echo -e "\033[0;32m[✓]\033[0m $1"; }
step(){ echo -e "\033[1;33m[$(date +%H:%M:%S)]\033[0m $1"; }

step "1/4 下载 mihomo..."
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) MI="mihomo-linux-amd64-v1.19.30.gz";;
  aarch64|arm64) MI="mihomo-linux-arm64-v1.19.30.gz";;
  *) echo "不支持架构: $ARCH"; exit 1;;
esac
mkdir -p /usr/local/bin
if [ ! -f /usr/local/bin/mihomo ]; then
  cd /tmp
  curl -fsSL --connect-timeout 20 -o mihomo.gz \
    "https://github.com/MetaCubeX/mihomo/releases/download/v1.19.30/${MI}"
  gunzip -f mihomo.gz
  chmod +x mihomo
  mv mihomo /usr/local/bin/mihomo
fi
/usr/local/bin/mihomo -v | head -1
info "mihomo 就绪"

step "2/4 生成配置..."
mkdir -p /etc/mihomo
cat > /etc/mihomo/config.yaml <<EOF
mixed-port: ${LISTEN_PORT}
allow-lan: false
mode: rule
log-level: warning

proxies:
  - name: "snell-out"
    type: snell
    server: ${SERVER}
    port: ${PORT}
    psk: "${PSK}"
    version: ${VERSION}
    reuse: true

rules:
  - MATCH,snell-out
EOF
info "配置已写入 /etc/mihomo/config.yaml"

step "3/4 配置 systemd 服务..."
cat > /etc/systemd/system/mihomo.service <<EOF
[Unit]
Description=mihomo snell proxy
After=network.target

[Service]
ExecStart=/usr/local/bin/mihomo -f /etc/mihomo/config.yaml -d /etc/mihomo
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable mihomo >/dev/null 2>&1 || true
systemctl restart mihomo
info "mihomo 服务已启动"

step "4/4 验证..."
sleep 2
systemctl is-active mihomo || { journalctl -u mihomo -n 20; exit 1; }
curl -x "http://127.0.0.1:${LISTEN_PORT}" --connect-timeout 15 -s -o /dev/null -w "YouTube HTTP:%{http_code}\n" "https://www.youtube.com" || true
curl -x "http://127.0.0.1:${LISTEN_PORT}" --connect-timeout 15 -s "https://ipinfo.io/ip" && echo " <- 代理出口 IP"
info "完成！请在 config.yaml 填入  proxy: \"http://127.0.0.1:${LISTEN_PORT}\""
