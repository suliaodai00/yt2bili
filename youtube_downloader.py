#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YouTube 统一专用下载器模块 (youtube_downloader.py)
集中管理：
  1. 认证策略：公开视频(mweb + PO Token) -> Firefox Profile -> youtube_cookies.txt 兼容 fallback
  2. PO Token 自动供给与连接
  3. Proxy 统一配置
  4. 速率限制 (sleep-requests / sleep-interval)
  5. 智能重试与精准错误分类识别 (BOT_CHECK, LOGIN_REQUIRED, HTTP_403, HTTP_429, NETWORK_TIMEOUT)
  6. 统一提取 Metadata / 下载视频 / 下载字幕

CLI 用法:
  python3 youtube_downloader.py metadata "URL"
  python3 youtube_downloader.py download "URL" [--output-dir DIR] [--sub-only] [--video-only]
  python3 youtube_downloader.py test "URL"
  python3 youtube_downloader.py status
"""

import sys, os, json, re, time, subprocess, shutil
from typing import Dict, Any, Optional, List, Tuple, Callable

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "output", "downloads")
DEFAULT_SUBTITLE_DIR = os.path.join(SCRIPT_DIR, "output", "subtitles")
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")
DEFAULT_POT_URL = "http://127.0.0.1:4416"
DEFAULT_FIREFOX_PROFILE = os.path.join(SCRIPT_DIR, "data", "youtube-firefox")
DEFAULT_COOKIES_TXT = os.path.join(SCRIPT_DIR, "youtube_cookies.txt")

class YouTubeError(Exception):
    def __init__(self, code: str, message: str, raw_error: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.raw_error = raw_error

class YouTubeDownloader:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH, yt_dlp_bin: Optional[str] = None):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.yt_dlp_bin = yt_dlp_bin or self._find_ytdlp()
        
        yt_cfg = self.config.get("youtube", {})
        self.proxy = self.config.get("proxy", "")
        self.client = yt_cfg.get("client", "mweb")
        self.browser = yt_cfg.get("browser", "firefox")
        self.browser_profile = yt_cfg.get("browser_profile", DEFAULT_FIREFOX_PROFILE)
        self.cookies_file = yt_cfg.get("cookies_file", DEFAULT_COOKIES_TXT)
        self.pot_provider_url = yt_cfg.get("pot_provider_url", DEFAULT_POT_URL)
        self.sleep_requests = yt_cfg.get("sleep_requests", 0)
        self.sleep_interval = yt_cfg.get("sleep_interval", 0)
        self.max_sleep_interval = yt_cfg.get("max_sleep_interval", 0)
        self.max_retries = yt_cfg.get("retries", 2)

    def _load_config(self, path: str) -> dict:
        if not os.path.exists(path):
            return {}
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def _find_ytdlp(self) -> str:
        venv_ytdlp = os.path.join(SCRIPT_DIR, ".venv", "bin", "yt-dlp")
        if os.path.exists(venv_ytdlp) and os.access(venv_ytdlp, os.X_OK):
            return venv_ytdlp
        which = shutil.which("yt-dlp")
        if which:
            return which
        return "yt-dlp"

    def check_pot_provider(self) -> bool:
        try:
            import urllib.request
            req = urllib.request.Request(f"{self.pot_provider_url}/ping", headers={"User-Agent": "yt2bili"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                return "version" in data or "server_uptime" in data
        except Exception:
            return False

    def check_firefox_profile(self) -> Tuple[bool, int]:
        if not os.path.exists(self.browser_profile):
            return False, 0
        db = os.path.join(self.browser_profile, "cookies.sqlite")
        if not os.path.exists(db):
            return True, 0
        try:
            import sqlite3
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM moz_cookies WHERE host LIKE '%youtube%' OR host LIKE '%google%';")
            row = cur.fetchone()
            conn.close()
            return True, row[0] if row else 0
        except Exception:
            return True, 0

    def get_auth_strategy(self, mode: str = "auto") -> List[Tuple[str, List[str]]]:
        strategies = []
        
        # 1. 公开模式 (mweb + POT)
        pot_available = self.check_pot_provider()
        public_args = ["--extractor-args", f"youtube:player_client={self.client}"]
        if pot_available:
            public_args.extend(["--extractor-args", f"youtubepot-bgutilhttp:base_url={self.pot_provider_url}"])
        strategies.append(("public_pot", public_args))

        # 2. Firefox Profile
        profile_exists, count = self.check_firefox_profile()
        if profile_exists and count > 0:
            strategies.append(("firefox_profile", ["--cookies-from-browser", f"firefox:{self.browser_profile}"]))

        # 3. cookies.txt fallback
        if os.path.exists(self.cookies_file) and os.path.getsize(self.cookies_file) > 0:
            strategies.append(("cookies_file", ["--cookies", self.cookies_file]))

        if mode == "auth_only":
            return [s for s in strategies if s[0] != "public_pot"]
        elif mode == "public_only":
            return [s for s in strategies if s[0] == "public_pot"]

        return strategies

    def _classify_error(self, stderr: str) -> str:
        s = stderr.lower()
        if "sign in to confirm you’re not a bot" in s or "confirm you're not a bot" in s or "bot check" in s:
            return "BOT_CHECK_SIGN_IN"
        if "login_required" in s or "this video is private" in s or "sign in to confirm your age" in s:
            return "LOGIN_REQUIRED"
        if "http error 429" in s or "too many requests" in s:
            return "HTTP_429_RATE_LIMITED"
        if "http error 403" in s or "forbidden" in s:
            return "HTTP_403_FORBIDDEN"
        if "timed out" in s or "connection refused" in s or "temporary failure in name resolution" in s:
            return "NETWORK_TIMEOUT"
        if "unsupported url" in s or "is not a valid url" in s:
            return "INVALID_URL"
        return "DOWNLOAD_ERROR"

    def _build_base_cmd(self, rate_limit: bool = False) -> List[str]:
        cmd = [self.yt_dlp_bin, "--no-warnings", "--no-colors"]
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])
        if rate_limit and self.sleep_requests > 0:
            cmd.extend([
                "--sleep-requests", str(self.sleep_requests),
                "--sleep-interval", str(self.sleep_interval),
                "--max-sleep-interval", str(self.max_sleep_interval)
            ])
        return cmd

    def get_metadata(self, url: str, log_func: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        strategies = self.get_auth_strategy()
        last_err = ""
        last_code = "UNKNOWN_ERROR"

        for strat_name, strat_args in strategies:
            if log_func:
                log_func(f"🔍 正在获取元数据 (尝试认证模式: {strat_name})...")
            
            cmd = self._build_base_cmd(rate_limit=False)
            cmd.extend(strat_args)
            cmd.extend(["--dump-json", "--skip-download", url])

            # 公开模式超时阈值 6s，快速故障转移到 Firefox Profile
            timeout_val = 6 if strat_name == "public_pot" else 25

            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_val)
                if res.returncode == 0 and res.stdout.strip():
                    data = json.loads(res.stdout.strip())
                    if log_func:
                        log_func(f"✅ 获取元数据成功: {data.get('title', 'No Title')} [{data.get('id', '')}]")
                    return {
                        "id": data.get("id", ""),
                        "title": data.get("title", ""),
                        "description": data.get("description", ""),
                        "duration": data.get("duration", 0),
                        "uploader": data.get("uploader", ""),
                        "upload_date": data.get("upload_date", ""),
                        "thumbnail": data.get("thumbnail", ""),
                        "tags": data.get("tags", []),
                        "auth_mode_used": strat_name,
                        "raw": data
                    }
                else:
                    err_txt = res.stderr or res.stdout
                    last_err = err_txt
                    last_code = self._classify_error(err_txt)
                    if log_func:
                        log_func(f"⚠️ 模式 {strat_name} 失败 [{last_code}]: {err_txt.strip()[:80]}")
            except subprocess.TimeoutExpired:
                last_code = "NETWORK_TIMEOUT"
                last_err = f"Metadata request timed out ({timeout_val}s)"
                if log_func:
                    log_func(f"⚠️ 模式 {strat_name} 响应超时，自动切换下一策略...")
            except Exception as e:
                last_err = str(e)
                if log_func:
                    log_func(f"⚠️ 模式 {strat_name} 异常: {e}")

        raise YouTubeError(last_code, f"无法获取视频元数据: {last_code}", last_err)

    def download(self, 
                 url: str, 
                 download_dir: str = DEFAULT_DOWNLOAD_DIR,
                 subtitle_dir: str = DEFAULT_SUBTITLE_DIR,
                 video_only: bool = False,
                 sub_only: bool = False,
                 progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
                 log_func: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        os.makedirs(download_dir, exist_ok=True)
        os.makedirs(subtitle_dir, exist_ok=True)

        strategies = self.get_auth_strategy()
        last_err = ""
        last_code = "UNKNOWN_ERROR"

        for strat_name, strat_args in strategies:
            if log_func:
                log_func(f"🚀 开始下载任务 (认证模式: {strat_name})...")
            
            cmd = self._build_base_cmd(rate_limit=False)
            cmd.extend(strat_args)
            
            cmd.extend([
                "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
                "--merge-output-format", "mp4",
                "-o", os.path.join(download_dir, "%(id)s.%(ext)s")
            ])

            if not video_only:
                cmd.extend([
                    "--write-subs", "--write-auto-subs",
                    "--sub-langs", "en,en-orig,en-US",
                    "--sub-format", "vtt/srt/best",
                    "--embed-thumbnail"
                ])

            if sub_only:
                cmd.append("--skip-download")

            timeout_val = 10 if strat_name == "public_pot" else 600

            for attempt in range(1, self.max_retries + 2):
                if attempt > 1 and log_func:
                    log_func(f"🔄 正在进行第 {attempt} 次重试...")
                
                try:
                    res = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout_val)
                    out_text = (res.stdout or "") + "\n" + (res.stderr or "")

                    if res.returncode == 0:
                        vid_match = re.search(r"\[download\] Destination: .*?([a-zA-Z0-9_-]{11})\.(mp4|mkv|webm)", out_text)
                        vid = vid_match.group(1) if vid_match else ""
                        if not vid:
                            m = re.search(r"([a-zA-Z0-9_-]{11})\.mp4", out_text)
                            if m: vid = m.group(1)

                        found_video = None
                        found_vtt = None
                        if vid:
                            v_path = os.path.join(download_dir, f"{vid}.mp4")
                            if os.path.exists(v_path):
                                found_video = v_path
                            for f in os.listdir(download_dir):
                                if f.startswith(vid) and f.endswith(".vtt"):
                                    found_vtt = os.path.join(download_dir, f)
                                    break

                        if log_func:
                            log_func(f"✅ 下载完成 (视频: {os.path.basename(found_video) if found_video else '未找到'}, 字幕: {os.path.basename(found_vtt) if found_vtt else '无'})")

                        return {
                            "video_id": vid,
                            "video_file": found_video,
                            "subtitle_file": found_vtt,
                            "auth_mode_used": strat_name,
                            "status": "success"
                        }
                    else:
                        last_err = out_text
                        last_code = self._classify_error(out_text)
                        if log_func:
                            log_func(f"⚠️ 模式 {strat_name} 下载未通过 [{last_code}]")
                        
                        # 触发账号/反爬机制时直接换策略
                        if last_code in ["BOT_CHECK_SIGN_IN", "LOGIN_REQUIRED", "HTTP_403"]:
                            break
                        
                        time.sleep(2 * attempt)

                except subprocess.TimeoutExpired:
                    last_code = "NETWORK_TIMEOUT"
                    last_err = f"Download timed out after {timeout_val}s"
                    if log_func:
                        log_func(f"⚠️ 模式 {strat_name} 下载超时，自动切入下一模式...")
                    break
                except Exception as e:
                    last_err = str(e)
                    if log_func:
                        log_func(f"⚠️ 发生异常: {e}")
                    time.sleep(2)

        raise YouTubeError(last_code, f"YouTube 下载最终失败: {last_code}", last_err)

def main():
    if len(sys.argv) < 2:
        print("用法: python3 youtube_downloader.py <metadata|download|test|status> [URL]")
        sys.exit(1)

    action = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else ""
    downloader = YouTubeDownloader()

    def stdout_log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    if action == "status":
        pot_ok = downloader.check_pot_provider()
        prof_ok, cookie_count = downloader.check_firefox_profile()
        cook_txt_ok = os.path.exists(downloader.cookies_file)
        print(json.dumps({
            "pot_provider_online": pot_ok,
            "firefox_profile_exists": prof_ok,
            "firefox_cookie_count": cookie_count,
            "cookies_txt_exists": cook_txt_ok,
            "strategies_available": [s[0] for s in downloader.get_auth_strategy()]
        }, indent=2, ensure_ascii=False))
        sys.exit(0)

    if not url:
        print("请提供 YouTube URL")
        sys.exit(1)

    if action == "metadata" or action == "test":
        try:
            meta = downloader.get_metadata(url, log_func=stdout_log)
            print(json.dumps(meta, indent=2, ensure_ascii=False))
        except YouTubeError as e:
            print(json.dumps({"status": "error", "code": e.code, "message": e.message, "details": e.raw_error[-300:]}, indent=2, ensure_ascii=False))
            sys.exit(1)

    elif action == "download":
        try:
            res = downloader.download(url, log_func=stdout_log)
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except YouTubeError as e:
            print(json.dumps({"status": "error", "code": e.code, "message": e.message, "details": e.raw_error[-300:]}, indent=2, ensure_ascii=False))
            sys.exit(1)
    else:
        print(f"未知操作: {action}")
        sys.exit(1)

if __name__ == "__main__":
    main()
