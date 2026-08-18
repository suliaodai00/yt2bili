#!/bin/bash
# yt2bili 项目一键重建脚本
# 用法: 在目标机器上解压 yt2bili_light.tar.gz 后运行此脚本
set -euo pipefail
cd "$(dirname "$0")"

echo "=== yt2bili 项目重建 ==="

# 1. 安装系统依赖
echo "[1/5] 安装系统依赖..."
apt-get update -qq && apt-get install -y -qq ffmpeg python3 python3-venv 2>/dev/null || apk add ffmpeg python3 py3-pip 2>/dev/null || true

# 2. 创建虚拟环境
echo "[2/5] 创建 Python 虚拟环境..."
python3 -m venv .venv

# 3. 安装 pip 包
echo "[3/5] 安装 Python 依赖..."
.venv/bin/pip install -q --upgrade pip yt-dlp biliup pyyaml flask qrcode pillow

# 4. 安装 Ollama (如需要)
echo "[4/5] 安装 Ollama..."
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
    echo "等待 Ollama 启动..."
    sleep 5
    # 8核/12G 内存推荐 qwen2.5:3b：速度快约2.5~3倍，字幕翻译足够
    ollama pull qwen2.5:3b
fi

# 开启 Ollama 并行推理（配合 translate.py 的 ollama_concurrency: 3）
if command -v systemctl &>/dev/null; then
    mkdir -p /etc/systemd/system/ollama.service.d
    cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment=OLLAMA_NUM_PARALLEL=3
EOF
    systemctl daemon-reload
    systemctl restart ollama 2>/dev/null || true
fi

# 5. 安装 Deno (解决 YouTube EJS 挑战)
echo "[5/5] 安装 Deno..."
if ! command -v deno &>/dev/null; then
    curl -fsSL https://deno.land/install.sh | sh
fi

echo ""
echo "===================="
echo "✅ 重建完成！"
echo ""
echo "使用说明:"
echo "  bash yt2bili.sh --url \"YouTube链接\"   # 命令行"
echo "  python3 webapp.py 5000                 # 启动 Web 面板"
echo "  然后访问 http://127.0.0.1:5000"
echo "===================="