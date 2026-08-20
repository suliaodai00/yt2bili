#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whisper 语音识别后备: 当 YouTube 无英文字幕时, 从音频识别出英文字幕
用法: python3 transcribe.py <video> <outdir> <venv_dir>
"""
import sys, os, subprocess

def main():
    if len(sys.argv) < 3:
        print("用法: transcribe.py <video> <outdir> [venv_dir]"); sys.exit(1)
    video, outdir = sys.argv[1], sys.argv[2]
    venv = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.realpath(__file__)), '.venv')
    os.makedirs(outdir, exist_ok=True)
    vid = os.path.splitext(os.path.basename(video))[0]
    audio = f'/tmp/yt2bili_{vid}.wav'
    out_srt = os.path.join(outdir, f'{vid}_whisper.srt')

    # 1. 提取音频
    subprocess.run(['ffmpeg', '-y', '-i', video, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio],
                   check=True, capture_output=True)

    # 2. 写一个临时脚本执行 whisper（避免 -c 的转义地狱）
    script = os.path.join('/tmp', f'whisper_run_{vid}.py')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(f'''import sys
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cpu", compute_type="int8")
segments, info = model.transcribe(r"{audio}", language="en", beam_size=5)
with open(r"{out_srt}", "w", encoding="utf-8") as f:
    for i, seg in enumerate(segments, 1):
        st = seg.start; en = seg.end
        h = int(st // 3600); m = int(st % 3600 // 60); s = st % 60
        eh = int(en // 3600); em = int(en % 3600 // 60); es = en % 60
        start_ts = f"{{h:02d}}:{{m:02d}}:{{s:06.3f}}".replace(".", ",")
        end_ts = f"{{eh:02d}}:{{em:02d}}:{{es:06.3f}}".replace(".", ",")
        f.write(f"{{i}}\\n{{start_ts}} --> {{end_ts}}\\n{{seg.text.strip()}}\\n\\n")
print("whisper done:", info.language)
''')

    py = os.path.join(venv, 'bin/python3')
    subprocess.run([py, script], check=True)

    # 3. 清理
    os.remove(audio)
    if os.path.exists(script):
        os.remove(script)
    print(out_srt)

if __name__ == '__main__':
    main()