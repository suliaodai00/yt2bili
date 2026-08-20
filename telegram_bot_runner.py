#!/usr/bin/env python3
"""yt2bili Telegram Bot 独立守护进程"""
import os, sys, re, json, urllib.request, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from telegram_bot import _read_token, _save_chat_id, DATA_FILE

token = _read_token()
if not token:
    print("[Telegram] No token found in config.yaml, exiting...")
    sys.exit(0)

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update

async def cmd_start(update: Update, context):
    chat_id = update.effective_chat.id
    _save_chat_id(chat_id)
    await update.message.reply_text(
        "🤖 *yt2bili Bot 已就绪*\n\n"
        "✅ 已将当前会话绑定为任务完成通知目标！\n\n"
        "你可以直接发送 YouTube 链接，自动执行：\n"
        "⬇️ 下载 → 🌐 双语翻译 → 🎬 压制 → 🚀 上传 B 站\n\n"
        "命令：\n"
        "/status — 查看任务队列与统计",
        parse_mode='Markdown'
    )

async def cmd_status(update: Update, context):
    chat_id = update.effective_chat.id
    _save_chat_id(chat_id)
    try:
        with open(DATA_FILE, encoding='utf-8') as f:
            tasks = json.load(f)
    except Exception:
        tasks = {}
    running = sum(1 for t in tasks.values() if t.get('status') == 'running')
    queued = sum(1 for t in tasks.values() if t.get('status') == 'queued')
    done = sum(1 for t in tasks.values() if t.get('status') == 'done')
    err = sum(1 for t in tasks.values() if t.get('status') == 'error')
    await update.message.reply_text(
        f"📊 *任务队列状态*\n"
        f"⏳ 排队: {queued} | 🏃 运行: {running}\n"
        f"✅ 成功: {done} | ❌ 失败: {err}",
        parse_mode='Markdown'
    )

async def cmd_msg(update: Update, context):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    _save_chat_id(chat_id)
    yt = r'(https?:\/\/(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)[\w-]+)'
    m = re.search(yt, text)
    if not m:
        await update.message.reply_text("❌ 请发送有效的 YouTube 视频链接")
        return
    url = m.group(1)
    req = urllib.request.Request(
        "http://127.0.0.1:5000/start",
        data=json.dumps({"url": url}).encode('utf-8'),
        headers={"Content-Type": "application/json"},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tid = data.get('task_id', '')
        await update.message.reply_text(
            f"✅ *已成功创建任务*\n"
            f"🆔 `{tid}`\n\n"
            f"🚀 已进入自动化工作流，全部完成时会在此通知你！",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 任务创建失败: {e}")

def main():
    print(f"[Telegram] 正在连接 Telegram API (Token: {token[:8]}...)...")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_msg))
    print("[Telegram] Bot 启动成功，正在轮询消息...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
