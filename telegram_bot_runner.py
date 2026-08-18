#!/usr/bin/env python3
import asyncio, sys
sys.path.insert(0, "/opt/yt2bili")
from telegram_bot import _read_token, _read_chat_id, _save_chat_id
token = _read_token()
if not token:
    print("No token")
    sys.exit(1)

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update

async def cmd_start(update, context):
    await update.message.reply_text(
        "\U0001F916 *yt2bili Bot*\n\u76f4\u63a5\u53d1 YouTube \u94fe\u63a5\uff0c\u81ea\u52a8\u4e0b\u8f7d\u2192\u7ffb\u8bd1\u2192\u4e0a\u4f20 B \u7ad9",
        parse_mode='Markdown'
    )

async def cmd_status(update, context):
    import json
    try:
        with open("/opt/yt2bili/output/tasks.json", encoding='utf-8') as f:
            tasks = json.load(f)
    except:
        tasks = {}
    running = sum(1 for t in tasks.values() if t.get('status') == 'running')
    queued = sum(1 for t in tasks.values() if t.get('status') == 'queued')
    done = sum(1 for t in tasks.values() if t.get('status') == 'done')
    err = sum(1 for t in tasks.values() if t.get('status') == 'error')
    await update.message.reply_text(
        f"\U0001F4CA *\u4efb\u52a1\u961f\u5217*\n\u23f3 \u6392\u961f: {queued} | \U0001F3C3 \u8fd0\u884c: {running}\n\u2705 \u5b8c\u6210: {done} | \u274c \u5931\u8d25: {err}",
        parse_mode='Markdown'
    )

async def cmd_msg(update, context):
    import re
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    yt = r'(https?://(?:www\\.)?(?:youtube\\.com/watch\\?v=|youtu\\.be/)[\\w-]+)'
    m = re.search(yt, text)
    if not m:
        await update.message.reply_text("\u274c \u8bf7\u53d1\u9001 YouTube \u94fe\u63a5")
        return
    url = m.group(1)
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:5000/start",
        data=('{"url":"' + url + '")}').encode(),
        headers={"Content-Type": "application/json"},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tid = data.get('task_id', '')
        _save_chat_id(chat_id)
        await update.message.reply_text(f"\u2705 \u5df2\u63a5\u6536\u4efb\u52a1\n\U0001F194 `{tid}`\n\n\u5f00\u59cb\u4e0b\u8f7d\u2192\u7ffb\u8bd1\u2192\u4e0a\u4f20\uff0c\u5b8c\u6210\u540e\u901a\u77e5\u4f60", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"\u274c \u521b\u5efa\u4efb\u52a1\u5931\u8d25: {e}")

app = Application.builder().token(token).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_msg))
print("[Telegram] Bot \u542f\u52a8\u5b8c\u6210")
app.run_polling(allowed_updates=Update.ALL_TYPES)
