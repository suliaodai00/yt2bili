# yt2bili 监控面板 v2 部署说明

在原有「下载 → 翻译 → 上传」基础上，新增：

- **服务器资源监控**：CPU / 内存 / 磁盘 / 负载 / Ollama 在线状态，5 秒刷新
- **任务统计**：总数、进行中、成功、失败、成功率、累计翻译字幕数
- **任务持久化**：任务记录写入 `output/tasks.json`，服务重启不丢失
- **失败重试**：失败/已完成任务一键重试
- **历史清理**：一键清空已完成任务
- **删除已下载文件**：每个任务可一键删除其视频/字幕/成品，释放磁盘空间
- **Cookie 配置查看**：面板显示 YouTube / Bilibili cookie 文件放置路径与配置状态
- **Cookie 在线登录**：面板内直接扫码登录 Bilibili 自动保存 cookies.json；上传 Netscape 格式文件即可配置 YouTube cookie
- **新前端监控面板**：统计卡片 + 资源曲线 + 任务筛选/详情日志

## 新增 / 修改的文件

| 文件 | 说明 |
|------|------|
| `webapp.py` | 后端主程序（重写，新增监控/统计/重试/清空 API） |
| `templates/index.html` | 新前端页面 |
| `static/app.js` | 前端逻辑 |
| `static/style.css` | 前端样式 |
| `translate.py` | 新增并发翻译（`ollama_concurrency` 配置） |
| `upload.py` / `transcribe.py` / `yt2bili.sh` | 未改动，可保留 |

## 部署步骤（VPS 上 /opt/yt2bili）

### 方式一：一键部署脚本（推荐）

**远程一键**（自动克隆到 `/opt/yt2bili` 后部署）：

```bash
curl -fsSL https://raw.githubusercontent.com/suliaodai00/yt2bili/main/deploy.sh | bash
```

或手动 clone：

```bash
git clone https://github.com/suliaodai00/yt2bili.git /opt/yt2bili
cd /opt/yt2bili
bash deploy.sh
```

`deploy.sh` 幂等可重复执行，自动完成：
系统依赖（ffmpeg/python3-venv）→ `.venv` + Python 依赖 → Ollama 安装与并行配置（`OLLAMA_NUM_PARALLEL=3`）→ 翻译模型自动选择（内存 ≥14G 用 `qwen2.5:7b`，否则 `qwen2.5:3b`）→ 生成 `config.yaml` → 安装 systemd 服务（开机自启）→ 健康检查。

后续更新：`cd /opt/yt2bili && git pull && bash deploy.sh`（自动重启服务），或重跑远程一键命令。

### 方式二：tar 包

1. **备份旧版本**（可选）：
   ```bash
   cd /opt/yt2bili && tar czf ~/yt2bili-bak-$(date +%F).tar.gz webapp.py 2>/dev/null
   ```

2. **上传新版本**：把 `webapp.py`、`templates/`、`static/` 传到 `/opt/yt2bili/` 下
   ```bash
   # 方式一：直接用新包覆盖
   tar xzf yt2bili-v2.tar.gz -C /opt/yt2bili
   ```

3. **重启服务**（根据原启动方式二选一）：
   ```bash
   # 若用 systemd 管理
   sudo systemctl restart yt2bili
   # 若用 nohup / 手动进程：找到原进程并重启
   pkill -f webapp.py
   cd /opt/yt2bili && nohup python3 webapp.py 5000 > webapp.log 2>&1 &
   ```

4. **验证**：访问 `https://yt.199-47-242-58.sslip.io`，应看到监控面板。

## 新接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/stats` | GET | 任务统计 |
| `/api/system` | GET | 服务器资源监控 |
| `/api/retry` | POST | 重试失败任务 `{"task_id":"t..."}` |
| `/api/clear` | POST | 清空已完成 `{"only_finished":true}` |
| `/api/delete-files` | POST | 删除任务已下载文件 `{"task_id":"t..."}`，保留任务记录 |
| `/api/cookies` | GET | 返回 YouTube/Bilibili cookie 文件路径与状态 |
| `/api/bili-login/start` | POST | 生成 Bilibili 扫码登录二维码 |
| `/api/bili-login/status` | GET | 轮询扫码状态，成功后自动写 cookies.json |
| `/api/yt-cookie` | POST | 上传 YouTube cookie 文件（multipart `file`） |
| `/start` `/status` `/tasks` | 兼容保留 | 原有接口不变 |

## 注意事项

- 首次启动自动创建 `output/` 目录；`tasks.json` 在任务状态变化时自动保存
- 若需服务开机自启，建议配置 systemd service（参考 `setup.sh`）
- 失败任务重试会重新走完整流程
- **扫码登录依赖**：Bilibili 面板扫码登录需要 `.venv` 里装有 `qrcode` + `pillow`（`setup.sh` 已包含；手动安装：`.venv/bin/pip install qrcode pillow`）。若未安装，页面会提示安装，不影响其他功能

## 翻译加速（v2.1 新增）

翻译慢的主要瓶颈是 **7B 模型在纯 CPU 上的推理速度**。v2.1 的 `translate.py` 支持两个优化，均在 `config.yaml` 配置：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `ollama_concurrency` | `1` | 并行翻译的路数。8 核 CPU 建议 `2~3` |
| `ollama_batch_size` | `20` | 每批字幕条数 |

### 关键前提：Ollama 服务端要允许并行

客户端并发后，Ollama 服务端必须开启并行才有真实加速。启动 Ollama 时设置环境变量（值 ≥ `ollama_concurrency`）：

```bash
OLLAMA_NUM_PARALLEL=3 ollama serve
```

已用 systemd 管理 Ollama 的话，在 service 文件的 `[Service]` 段加：

```ini
Environment=OLLAMA_NUM_PARALLEL=3
```

然后 `systemctl restart ollama`。

### 模型选择与内存对照

| 模型 | 单实例内存 | 8C/12G 建议并发 | 相对 7B 提速 |
|------|-----------|----------------|-------------|
| `qwen2.5:7b`（现状） | ~5GB | 1（不并发，防 OOM） | 1x |
| `qwen2.5:3b` | ~2GB | 3 | **约 6~9x** |
| `qwen3:8b` | ~5GB | 1~2 | 1~2x |

**推荐配置（8核/12G 内存）**：改用 `qwen2.5:3b`，`ollama_concurrency: 3`，`ollama_batch_size: 16`。

```yaml
ollama_model: qwen2.5:3b
ollama_concurrency: 3
ollama_batch_size: 16
```

注意：并发路数越多，CPU 争抢与内存占用越高。若出现 OOM 或速度反降，先降 `ollama_concurrency`。

- 改完 `config.yaml` 后无需重启面板，仅对后续新任务生效。
