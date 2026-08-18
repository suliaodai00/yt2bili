#!/bin/bash
# ============================================================
# yt2bili — 下载YouTube → AI双语翻译 → 上传Bilibili 一键流程
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
DOWNLOAD_DIR="$SCRIPT_DIR/output/downloads"
SUBTITLE_DIR="$SCRIPT_DIR/output/subtitles"
FINAL_DIR="$SCRIPT_DIR/output/final"
COOKIES="$SCRIPT_DIR/cookies.json"
CONFIG="$SCRIPT_DIR/config.yaml"
YOUTUBE_COOKIES="$SCRIPT_DIR/youtube_cookies.txt"

mkdir -p "$DOWNLOAD_DIR" "$SUBTITLE_DIR" "$FINAL_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info(){ echo -e "${GREEN}[✓]${NC} $1"; }
warn(){ echo -e "${YELLOW}[!]${NC} $1"; }
err(){ echo -e "${RED}[✗]${NC} $1"; exit 1; }

usage(){
cat <<'EOF'
用法: ./yt2bili.sh [选项]

  --url <URL>             YouTube 视频链接
  --title <TITLE>         B站标题 (默认=YouTube标题)
  --desc <DESC>           B站简介 (默认=YouTube简介)
  --tid <ID>              B站分区 (默认=config里的值, 171=科技)
  --tag <TAGS>            B站标签,逗号分隔
  --download-only         只下载
  --translate-only        只翻译 (需 --video)
  --upload-only           只上传 (需 --video)
  --video <FILE>          指定本地视频文件
  --setup                 安装依赖
  -h|--help               帮助
EOF
exit 0
}

URL=""; TITLE=""; DESC=""; TAGS=""; TID=""; VIDEO=""
DO_DL=true; DO_TR=true; DO_UP=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2;;
    --title) TITLE="$2"; shift 2;;
    --desc) DESC="$2"; shift 2;;
    --tid) TID="$2"; shift 2;;
    --tag) TAGS="$2"; shift 2;;
    --video) VIDEO="$2"; shift 2;;
    --download-only) DO_TR=false; DO_UP=false; shift;;
    --translate-only) DO_DL=false; DO_UP=false; shift;;
    --upload-only) DO_DL=false; DO_TR=false; shift;;
    --setup) bash "$SCRIPT_DIR/setup.sh" 2>/dev/null || setup; exit 0;;
    -h|--help) usage;;
    *) err "未知参数: $1";;
  esac
done

# ===== setup: 安装依赖 =====
setup(){
  info "安装系统依赖..."
  apt-get install -y ffmpeg 2>/dev/null || apk add ffmpeg 2>/dev/null || true
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  info "安装 Python 包 (yt-dlp/biliup/faster-whisper)..."
  "$VENV/bin/pip" install -q --upgrade pip yt-dlp biliup
  info "依赖完成"
  warn "若需要AI翻译,请确保 config.yaml 里的 gemini_api_key 已配置"
}

# ===== 读取 config 的辅助函数 =====
cfg(){ grep -E "^$1:" "$CONFIG" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"'; }

# ===== 1. 下载 YouTube =====
download(){
  local url="$1"
  info "下载YouTube: $url"
  local proxy_opt=""
  local px=$(cfg proxy); [ -n "$px" ] && proxy_opt="--proxy $px"
  local ck_opt=""
  [ -f "$YOUTUBE_COOKIES" ] && ck_opt="--cookies $YOUTUBE_COOKIES"

  # 获取元信息
  local meta
  meta=$("$VENV/bin/yt-dlp" $proxy_opt $ck_opt --print-json --skip-download "$url" 2>/dev/null) || err "获取视频信息失败"
  local vid=$(echo "$meta" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id','unknown'))")
  local vtitle=$(echo "$meta" | python3 -c "import sys,json;print(json.load(sys.stdin).get('title','video'))")
  [ -z "$TITLE" ] && TITLE="$vtitle"
  [ -z "$DESC" ] && DESC=$(echo "$meta" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print((d.get('description','') or '转载自YouTube')[:2000])
" || echo "转载自YouTube")

  # 下载视频+字幕+封面
  info "下载视频(含英文字幕/封面)..."
  "$VENV/bin/yt-dlp" $proxy_opt $ck_opt \
    -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" \
    --write-subs --write-auto-subs \
    --sub-langs "en" \
    --embed-subs --embed-thumbnail \
    -o "$DOWNLOAD_DIR/%(id)s.%(ext)s" \
    "$url" 2>&1 | tail -3

  # 定位文件
  VIDEO=$(ls "$DOWNLOAD_DIR/${vid}".mp4 2>/dev/null | head -1)
  [ -z "$VIDEO" ] && VIDEO=$(ls -t "$DOWNLOAD_DIR"/*.mp4 2>/dev/null | head -1)
  [ -z "$VIDEO" ] && err "未找到下载的视频"
  info "视频: $(basename "$VIDEO")"
  echo "$vid" > /tmp/yt2bili_vid.txt
  echo "$vtitle" > /tmp/yt2bili_title.txt
}

# ===== 2. AI双语翻译 (YouTube英文字幕→中文, 用Gemini) =====
translate(){
  local video="$1"
  local vid=$(basename "$video" | sed 's/\.[^.]*$//')
  info "AI双语翻译..."

  # 找英文字幕
  local en_sub="${DOWNLOAD_DIR}/${vid}.en.vtt"
  [ -f "$en_sub" ] || en_sub=$(ls "${DOWNLOAD_DIR}/${vid}".*.vtt 2>/dev/null | head -1)
  if [ -z "$en_sub" ] || [ ! -f "$en_sub" ]; then
    warn "未找到英文字幕，尝试语音识别..."
    en_sub=$(python3 "$SCRIPT_DIR/transcribe.py" "$video" "$SUBTITLE_DIR" "$VENV")
    [ -z "$en_sub" ] && warn "语音识别失败，跳过翻译" && return 0
  fi

  info "生成双语字幕: $en_sub"
  "$VENV/bin/python3" "$SCRIPT_DIR/translate.py" \
    "$en_sub" "$SUBTITLE_DIR/${vid}_bilingual.srt" "$CONFIG"
  info "双语字幕: $SUBTITLE_DIR/${vid}_bilingual.srt"
}

# ===== 3. 上传 Bilibili =====
upload(){
  local video="$1"
  local vid title desc tid tags
  vid=$(basename "$video" | sed 's/\.[^.]*$//')
  title="${TITLE:-$(cat /tmp/yt2bili_title.txt 2>/dev/null || echo '视频')}"
  desc="${DESC:-转载自YouTube}"
  tid="${TID:-$(python3 -c "import yaml;print(yaml.safe_load(open('$CONFIG'))['bilibili'].get('tid',171))" 2>/dev/null || echo 171)}"
  tags="${TAGS:-DeepSeek,AI}"

  info "上传到Bilibili: $title"
  [ -f "$COOKIES" ] || err "缺少 cookies.json,先运行登录"

  "$VENV/bin/python3" "$SCRIPT_DIR/upload.py" \
    --video "$video" \
    --cookie "$COOKIES" \
    --title "$title" \
    --desc "$desc" \
    --tid "$tid" \
    --tags "$tags" \
    --source "https://youtube.com/watch?v=$vid"
  info "上传完成"
}

# ===== 主流程 =====
if [ "$DO_DL" = true ] && [ -n "$URL" ]; then download "$URL"; fi
if [ "$DO_TR" = true ] && [ -n "$VIDEO" ]; then translate "$VIDEO"; fi
if [ "$DO_UP" = true ] && [ -n "$VIDEO" ]; then upload "$VIDEO"; fi
info "全部完成！"