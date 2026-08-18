# yt2bili

YouTube 下载 → 本地 Ollama 双语翻译 → Bilibili 一键上传 的 Web 监控面板。

## 功能

- 单页监控面板：任务统计 / 服务器资源监控（CPU/内存/磁盘/负载/Ollama 状态）
- 任务流水线：下载视频+英文字幕 → 本地 AI 双语翻译 → 烧录字幕 → 上传 Bilibili
- 任务管理：失败重试 / 一键清空 / 删除已下载文件（释放磁盘）
- **并发翻译**：`config.yaml` 配置 `ollama_concurrency`，8 核 12G 内存配 `qwen2.5:3b` 提速约 6~9 倍
- **Cookie 在线配置**：面板内扫码登录 Bilibili 自动保存 `cookies.json`；上传文件即可配置 YouTube cookie
- 任务持久化：记录写入 `output/tasks.json`，服务重启不丢失
- 移动端适配

## 部署（VPS，一键）

**远程一键**（最简，自动克隆到 `/opt/yt2bili`）：

```bash
curl -fsSL https://raw.githubusercontent.com/suliaodai00/yt2bili/main/deploy.sh | bash
```

或手动 clone 后执行：

```bash
git clone https://github.com/suliaodai00/yt2bili.git /opt/yt2bili
cd /opt/yt2bili
bash deploy.sh
```

`deploy.sh` 自动完成：系统依赖 → venv → Python 依赖 → Ollama（并行配置）→ 翻译模型（按内存自动选 `qwen2.5:3b/7b`）→ `config.yaml` → systemd 服务（开机自启）→ 健康检查。幂等，可重复执行。

后续更新：`cd /opt/yt2bili && git pull && bash deploy.sh`（或重跑远程一键命令）

详细说明见 [DEPLOY.md](DEPLOY.md)。

## 依赖

- Python 3 + `.venv`：`yt-dlp`、`biliup`、`flask`、`pyyaml`、`qrcode`、`pillow`
- `ffmpeg`（烧录字幕）、`Ollama`（本地翻译模型，推荐 `qwen2.5:3b`）
- `deno`（可选，解决 YouTube EJS 挑战）

## 配置

- `config.yaml.example` → `config.yaml`：翻译引擎、模型、并发、分区等
- `youtube_cookies.txt`：YouTube 登录 cookie（Netscape 格式，面板内上传）
- `cookies.json`：Bilibili 登录 cookie（面板内扫码生成）
- `output/`：下载、字幕、成品与任务记录（已 gitignore）
