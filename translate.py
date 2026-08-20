#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 双语字幕翻译: 英文字幕 -> 中英双语 SRT
支持两个免费引擎:
  1. ollama 本地模型 (完全免费无限, 推荐)  — via /api/generate
  2. gemini 免费API (有限额, 备用)
用法: python3 translate.py <input.srt|vtt> <output.srt> <config.yaml>
"""
import sys, os, json, re, time, threading
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.error, urllib.parse, yaml

def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def ts_to_sec(ts):
    parts = ts.strip().replace(",", ".").split(":")
    if len(parts) == 3:
        return float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0])*60 + float(parts[1])
    return 0.0

def sec_to_ts(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

def parse_vtt_with_words(content):
    """针对 YouTube 自动字幕 (带 <00:00:xx.xxx><c> word</c>) 进行词流提取与自然句聚合"""
    blocks = re.split(r"\n\s*\n", content.strip())
    seen_words = []
    
    for b in blocks:
        lines = [l.strip() for l in b.split("\n") if l.strip()]
        for l in lines:
            if "-->" in l or l.isdigit():
                continue
            tags = re.findall(r"<(\d{2}:\d{2}:\d{2}\.\d{3})><c>(.*?)</c>", l)
            for t_str, w in tags:
                w_clean = w.strip()
                if w_clean:
                    seen_words.append((ts_to_sec(t_str), w_clean))
                    
    if not seen_words:
        return []

    # 去除完全连续重复的词
    dedup_words = []
    for t, w in seen_words:
        if not dedup_words or dedup_words[-1][1] != w or abs(t - dedup_words[-1][0]) > 1.0:
            dedup_words.append((t, w))

    # 按标点或停顿聚合成句子
    segments = []
    cur_words = []
    cur_start = dedup_words[0][0] if dedup_words else 0
    
    for i, (t, w) in enumerate(dedup_words):
        cur_words.append(w)
        is_end = False
        if any(w.endswith(p) for p in [".", "?", "!", '."', '?"', '!"']):
            is_end = True
        elif i + 1 < len(dedup_words) and (dedup_words[i+1][0] - t) > 1.5:
            is_end = True
        elif len(cur_words) >= 16:
            is_end = True
            
        if is_end:
            end_t = dedup_words[i+1][0] if i + 1 < len(dedup_words) else t + 1.5
            if end_t - cur_start > 7.0:
                end_t = cur_start + 7.0
            sentence_txt = " ".join(cur_words)
            tm_str = f"{sec_to_ts(cur_start)} --> {sec_to_ts(end_t)}"
            segments.append({"num": len(segments) + 1, "time": tm_str, "text": sentence_txt})
            cur_words = []
            if i + 1 < len(dedup_words):
                cur_start = dedup_words[i+1][0]

    return segments

def parse_srt_or_vtt(path):
    """解析 SRT 或 VTT 为段列表 [{num, time, text}]"""
    with open(path, encoding="utf-8") as f:
        content = f.read()
        
    # 如果是 YouTube 自动生成的 VTT (含内联 word-level tags)，优先用聚合器
    if "<c>" in content and "</c>" in content:
        res = parse_vtt_with_words(content)
        if res:
            return res

    segments = []
    content = re.sub(r"^WEBVTT.*?(?=\d{2}:\d{2}:\d{2}[.,])", "", content, flags=re.S)
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if len(lines) < 2: continue
        tm = None
        for ln in lines:
            if "-->" in ln:
                tm = re.sub(r"\s+align:.*$", "", ln.strip())
                tm = tm.replace(",", ".")
                break
        if not tm: continue
        texts = [ln for ln in lines if "-->" not in ln and not ln.strip().isdigit()]
        text = " ".join(texts)
        text = re.sub(r"<[^>]+>", "", text).strip()
        if not text: continue
        num = seg_num_of(lines)
        if num == 0:
            num = len(segments) + 1
        segments.append({"num": num, "time": tm, "text": text})
    return segments

def seg_num_of(lines):
    for ln in lines:
        if ln.strip().isdigit():
            return int(ln.strip())
    return 0

# ============ Ollama (本地, 免费无限) ============
def ollama_batch(ollama_url, model, batch, batch_size):
    texts = [s["text"] for s in batch]
    prompt = f"""You are a professional subtitle translator. Translate the English subtitle texts into natural, concise Chinese (zh-CN) suitable for subtitles.

Return ONLY a valid JSON array with EXACTLY {len(batch)} elements, format:
[{{"index": 0, "zh": "中文翻译"}}, {{"index": 1, "zh": "..."}}, ...]

Rules:
- index must be 0-based sequential, matching each input text
- Keep technical terms (DeepSeek, API, GPU, Gemini, Claude, etc.) unchanged
- Natural spoken Chinese, concise (fits on screen), not literal
- Return ONLY the JSON array — no markdown, no code fences, no explanation

Texts to translate:
{json.dumps(texts, ensure_ascii=False)}"""
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.1, "num_predict": 4096}}).encode()
    req = urllib.request.Request(f"{ollama_url}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    return data.get("response", "")

def _ollama_parse_batch(raw):
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip()).removesuffix("```").strip()
    m = re.search(r"\[.*\]", raw, re.S)
    if not m: return []
    try:
        items = json.loads(m.group(0))
    except Exception:
        return []
    if isinstance(items, dict):
        for k in ("translations", "items"):
            if k in items: items = items[k]; break
    return items if isinstance(items, list) else []

def _ollama_apply(items, batch):
    ok = 0
    for it in items:
        if isinstance(it, dict):
            idx = it.get("index")
            if idx is not None and 0 <= idx < len(batch):
                zh = it.get("zh", it.get("translation", "")).strip()
                if zh:
                    batch[idx]["zh"] = zh
                    ok += 1
    return ok

def ollama_translate(ollama_url, model, segments, batch_size=20, concurrency=1):
    total = len(segments)
    n_batches = (total + batch_size - 1) // batch_size
    if concurrency < 1: concurrency = 1
    ok = 0
    ok_lock = threading.Lock()

    def work(idx):
        nonlocal ok
        start = idx * batch_size
        batch = segments[start:start+batch_size]
        for attempt in range(3):
            try:
                raw = ollama_batch(ollama_url, model, batch, batch_size)
                items = _ollama_parse_batch(raw)
                c = _ollama_apply(items, batch)
                with ok_lock:
                    ok += c
                    cur = ok
                print(f"[{idx+1}/{n_batches}] Ollama 翻译成功 {c}/{len(batch)}条 (已累计{cur})", flush=True)
                break
            except Exception as e:
                print(f"[批{idx+1}] Ollama 错误 (重试 {attempt+1}/3): {e}", flush=True)
                time.sleep(2)

    if n_batches <= 1 or concurrency == 1:
        for i in range(n_batches):
            work(i)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(work, range(n_batches)))
    return ok

# ============ Gemini (免费API, 有限额) ============
def gemini_batch(api_key, model, batch, batch_size):
    texts = [s["text"] for s in batch]
    prompt = f"""You are a professional subtitle translator. Translate the English subtitle texts into natural, concise Chinese (zh-CN).

Return ONLY a valid JSON array: [{{"index": 0, "zh": "..."}}, ...]
- index 0-based, matching input order
- Keep technical terms, natural Chinese
- Return ONLY the JSON array

Texts:
{json.dumps(texts, ensure_ascii=False)}"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192}
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json", "x-goog-api-key": api_key})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
        return data["candidates"][0]["content"]["parts"][0]["text"]

def gemini_translate(api_key, model, segments, batch_size=25):
    ok = 0
    for start in range(0, len(segments), batch_size):
        batch = segments[start:start+batch_size]
        for attempt in range(5):
            try:
                raw = gemini_batch(api_key, model, batch, batch_size).strip()
                raw = re.sub(r"^```[a-z]*\n?", "", raw.strip()).removesuffix("```").strip()
                items = json.loads(raw)
                if isinstance(items, dict):
                    for k in ("translations","items"):
                        if k in items: items=items[k]; break
                if isinstance(items, dict): items=[items]
                for it in items:
                    idx = it.get("index")
                    if idx is not None and 0 <= idx < len(batch):
                        zh = it.get("zh", it.get("translation","")).strip()
                        if zh:
                            batch[idx]["zh"] = zh
                            ok += 1
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    w = 40*(attempt+1)
                    print(f"  429限流,等待{w}s...", flush=True); time.sleep(w)
                else:
                    print(f"  批{start//batch_size+1} HTTP{e.code}", flush=True); break
            except Exception as e:
                print(f"  批{start//batch_size+1} 错:{e}", flush=True); break
        time.sleep(2)
    return ok

def main():
    if len(sys.argv) < 4:
        print("用法: translate.py <input> <output> <config.yaml>"); sys.exit(1)
    inp, out, cfg_path = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg = load_cfg(cfg_path)
    segments = parse_srt_or_vtt(inp)
    print(f"解析到 {len(segments)} 条字幕", flush=True)
    if not segments:
        print("未提取到任何字幕，退出", flush=True)
        sys.exit(1)

    engine = cfg.get("translation_engine", "ollama")
    done = 0
    if engine == "ollama":
        url = cfg.get("ollama_url", "http://127.0.0.1:11434")
        model = cfg.get("ollama_model", "qwen2.5:7b")
        print(f"使用本地翻译引擎: Ollama {model}", flush=True)
        batch_size = cfg.get("ollama_batch_size", 20)
        concurrency = cfg.get("ollama_concurrency", 1)
        print(f"翻译并发: {concurrency} 路 x 每批 {batch_size} 条", flush=True)
        done = ollama_translate(url, model, segments, batch_size=batch_size, concurrency=concurrency)
    else:
        api_key = cfg.get("gemini_api_key", "")
        model = cfg.get("gemini_model", "gemini-2.5-flash")
        print(f"使用翻译引擎: Gemini {model}", flush=True)
        done = gemini_translate(api_key, model, segments)

    if done == 0:
        print("⚠️ 翻译成功数为 0，不覆写输出文件，防止生成空翻译！", flush=True)
        sys.exit(1)

    with open(out, "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"{s['num']}\n{s['time']}\n{s['text']}")
            if s.get("zh"):
                f.write(f"\n{s['zh']}")
            f.write("\n\n")
    print(f"完成: {done}/{len(segments)} 已翻译 -> {out}", flush=True)

if __name__ == "__main__":
    main()
