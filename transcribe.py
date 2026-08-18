#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whisper 语音识别后备: 当 YouTube 无英文字幕时, 从音频识别出英文字幕
用法: python3 transcribe.py <video> <outdir> <venv_dir>
"""
import sys, os, subprocess, time

def main():
    if len(sys.argv) < 3:
        print("用法: transcribe.py <video> <outdir> [venv_dir]"); sys.exit(1)
    video, outdir = sys.argv[1], sys.argv[2]
    venv = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.realpath(__file__)), '.venv')
    os.makedirs(outdir, exist_ok=True)
    vid = os.path.splitext(os.path.basename(video))[0]

    # 提取音频
    audio = f'/tmp/yt2bili_{vid}.wav'
    subprocess.run(['ffmpeg', '-y', '-i', video, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio],
                   check=True, capture_output=True)

    # whisper
    py = os.path.join(venv, 'bin/python3')
    code = '''
import sys
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cpu", compute_type="int8")
segments, info = model.transcribe("%(audio)s", language="en", beam_size=5)
with open("%(out)s", "w", encoding="utf-8") as f:
    for i, seg in enumerate(segments, 1):
        st = seg.start; en = seg.end
        def ts(x):
            return f"{int(x//3600):02d}:{int(x%%3600//60):02d}:{x%%60:06.3f}"
        f.write(f"{i}\n{ts(st).replace(chr(46),chr(44))} --> {ts(en).replace(chr(46),chr(44))}\n{seg.text.strip()}\n\\n")
print("whisper done:", info.language)
''' % {'audio': audio, 'out': os.path.join(outdir, f'{vid}_whisper.srt')}
    subprocess.run([py, '-c', code], check=True)

    os.remove(audio)
    print(os.path.join(outdir, f'{vid}_whisper.srt'))

if __name__ == '__main__':
    main()