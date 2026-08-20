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
PYTHON="$VENV/bin/python3"
DOWNLOADER="$SCRIPT_DIR/youtube_downloader.py"
TRANSLATE_PY="$SCRIPT_DIR/translate.py"
TRANSCRIBE_PY="$SCRIPT_DIR/transcribe.py"
UPLOAD_PY="$SCRIPT_DIR/upload.py"

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
    --title) TITLE="$2"; MANUAL_TITLE="$2"; shift 2;;
    --desc) DESC="$2"; shift 2;;
    --tid) TID="$2"; shift 2;;
    --tag) TAGS="$2"; shift 2;;
    --video) VIDEO="$2"; shift 2;;
    --download-only) DO_TR=false; DO_UP=false; shift;;
    --translate-only) DO_DL=false; DO_UP=false; shift;;
    --upload-only) DO_DL=false; DO_TR=false; shift;;
    --setup) bash "$SCRIPT_DIR/setup.sh" 2>/dev/null || true; exit 0;;
    -h|--help) usage;;
    *) err "未知参数: $1";;
  esac
done

cfg(){ grep -E "^$1:" "$CONFIG" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"'; }

# ===== 1. 下载 YouTube (统一调用 youtube_downloader.py) =====
download(){
  local url="$1"
  info "获取视频信息并下载..."
  
  local meta_json
  meta_json="$("$PYTHON" "$DOWNLOADER" metadata "$url")" || {
    err "获取视频信息失败: $meta_json"
  }

  local vid
  vid=$(echo "$meta_json" | grep -o '"id": *"[^"]*"' | head -1 | cut -d'"' -f4)
  local y_title
  y_title=$(echo "$meta_json" | grep -o '"title": *"[^"]*"' | head -1 | cut -d'"' -f4)
  local y_desc
  y_desc=$(echo "$meta_json" | grep -o '"description": *"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")

  [ -z "$TITLE" ] && TITLE="$y_title"
  [ -z "$DESC" ] && DESC="$y_desc"

  info "视频原标题: $TITLE (ID: $vid)"

  # 如果未手动指定标题，调用本地 Ollama 将英文标题翻译为中文
  if [ -z "${MANUAL_TITLE:-}" ]; then
    local trans_title
    trans_title=$("$PYTHON" -c "
import sys, os, urllib.request, json, yaml
CONFIG = '$CONFIG'
text = '''$TITLE'''
url = 'http://127.0.0.1:11434'
model = 'qwen2.5:7b'
if os.path.exists(CONFIG):
    try:
        raw = yaml.safe_load(open(CONFIG, encoding='utf-8'))
        if isinstance(raw, dict):
            url = raw.get('ollama_url', url)
            model = raw.get('ollama_model', model)
    except Exception: pass
prompt = f'将以下英文视频标题翻译成中文，保持专业术语和专有名词不翻译，只返回翻译结果不要多余内容：\n\n{text}'
try:
    req = urllib.request.Request(f'{url}/api/generate', data=json.dumps({'model': model, 'prompt': prompt, 'stream': False, 'options': {'temperature': 0.1, 'num_predict': 100}}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
        t = resp.get('response', '').strip().strip('\"').strip(\"'\")
        if t: print(t)
        else: print(text)
except Exception: print(text)
" 2>/dev/null || echo "$TITLE")

    if [ -n "$trans_title" ] && [ "$trans_title" != "$TITLE" ]; then
      info "🌐 标题已翻译为中文: $trans_title"
      TITLE="$trans_title"
    fi
  fi

  # 统一下载
  local dl_json
  dl_json="$("$PYTHON" "$DOWNLOADER" download "$url")" || {
    err "下载失败: $dl_json"
  }

  local vid_file
  vid_file=$(echo "$dl_json" | grep -o '"video_file": *"[^"]*"' | head -1 | cut -d'"' -f4)

  if [ -z "$vid_file" ] || [ ! -f "$vid_file" ]; then
    vid_file="$DOWNLOAD_DIR/$vid.mp4"
  fi

  VIDEO="$vid_file"
  info "下载完成: $VIDEO"
}

# ===== 2. 翻译字幕 =====
translate(){
  local vid
  vid=$(basename "$VIDEO" | sed 's/\.[^.]*$//')
  local en_sub=""

  # 查找下载的英文字幕
  for f in "$DOWNLOAD_DIR/$vid"*.vtt "$DOWNLOAD_DIR/$vid"*.srt; do
    if [ -f "$f" ]; then en_sub="$f"; break; fi
  done

  # 若无英文字幕则使用 Whisper
  if [ -z "$en_sub" ]; then
    warn "未检测到官方/自动英文字幕, 正在使用 Whisper 提取语音..."
    en_sub="$SUBTITLE_DIR/${vid}_whisper.srt"
    "$PYTHON" "$TRANSCRIBE_PY" "$VIDEO" "$SUBTITLE_DIR" "$VENV" || true
  fi

  local out_srt="$SUBTITLE_DIR/${vid}_bilingual_full.srt"
  if [ -f "$en_sub" ] && [ -s "$en_sub" ]; then
    info "正在进行 AI 双语字幕翻译..."
    "$PYTHON" "$TRANSLATE_PY" "$en_sub" "$out_srt" "$CONFIG"
    SUBTITLE="$out_srt"
  else
    warn "未能生成英文字幕, 跳过字幕制作"
    SUBTITLE=""
  fi
}

# ===== 3. 压制字幕 =====
burn_subtitle(){
  local vid
  vid=$(basename "$VIDEO" | sed 's/\.[^.]*$//')
  local out_video="$FINAL_DIR/${vid}_final_bilingual.mp4"

  if [ -n "${SUBTITLE:-}" ] && [ -f "$SUBTITLE" ] && [ -s "$SUBTITLE" ]; then
    info "正在压制双语字幕到视频中..."
    ffmpeg -y -nostats -i "$VIDEO" \
      -vf "subtitles=$SUBTITLE:force_style='FontName=DejaVu Sans,FontSize=18,MarginV=40'" \
      -c:a copy "$out_video"
    FINAL_VIDEO="$out_video"
    info "压制完成: $FINAL_VIDEO"
  else
    info "无有效双语字幕, 使用原始视频"
    FINAL_VIDEO="$VIDEO"
  fi
}

# ===== 4. 上传 Bilibili =====
upload(){
  info "准备上传至 B 站..."
  local tid="${TID:-$(cfg tid)}"
  [ -z "$tid" ] && tid=171

  "$PYTHON" "$UPLOAD_PY" \
    --video "$FINAL_VIDEO" \
    --title "$TITLE" \
    --desc "${DESC:0:2000}" \
    --tid "$tid" \
    ${TAGS:+--tag "$TAGS"} \
    --cookies "$COOKIES"
}

# ===== 主执行流 =====
SUBTITLE=""
FINAL_VIDEO=""

if [ "$DO_DL" = true ]; then
  [ -z "$URL" ] && err "请提供 YouTube 链接: --url <URL>"
  download "$URL"
fi

if [ "$DO_TR" = true ]; then
  [ -z "$VIDEO" ] && err "缺少视频文件: --video <FILE>"
  translate
  burn_subtitle
else
  FINAL_VIDEO="${VIDEO:-}"
fi

if [ "$DO_UP" = true ]; then
  [ -z "$FINAL_VIDEO" ] && err "缺少最终视频文件"
  upload
fi

info "🎉 yt2bili 全流程执行成功！"
