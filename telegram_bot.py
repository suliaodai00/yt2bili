#!/usr/bin/env python3
"""yt2bili Telegram Bot — 私聊发 YouTube 链接自动下载翻译上传"""
import os, sys, json, time, threading, asyncio, re
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, 'output', 'tasks.json')
CONFIG = os.path.join(BASE, 'config.yaml')

# 用户白名单: None = 允许所有人
ALLOWED_USERS = None

# 引用 webapp 的 start_new_task 函数
_start_task_callback = None

def set_start_task_callback(cb):
    global _start_task_callback
    _start_task_callback = cb

def _read_config():
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            for line in f:
                if ':' in line:
                    k, v = line.split(':', 1)
                    cfg[k.strip()] = v.strip().strip('"')
    return cfg

def _read_token():
    cfg = _read_config()
    token = cfg.get('telegram_bot_token', '')
    if not token:
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    return token

def _read_chat_id():
    cfg = _read_config()
    cid = cfg.get('telegram_chat_id', '')
    if cid:
        try:
            return int(cid)
        except ValueError:
            pass
    return None

# === 命令处理 ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USERS and update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("\u26d4 \u672a\u6388\u6743\u7528\u6237")
        return
    await update.message.reply_text(
        "\U0001F916 *yt2bili Bot \u5df2\u5c31\u7eea*\n\n"
        "\u76f4\u63a5\u53d1\u9001 YouTube \u94fe\u63a5\u7ed9\u6211\uff0c\u81ea\u52a8\u5b8c\u6210\uff1a\n"
        "\u2b07\ufe0f \u4e0b\u8f7d \u2192 \U0001F310 \u53cc\u8bed\u7ffb\u8bd1 \u2192 \u2b06\ufe0f \u4e0a\u4f20 B \u7ad9\n\n"
        "\u547d\u4ee4\uff1a\n"
        "/status \u2014 \u67e5\u770b\u5f53\u524d\u961f\u5217\u72b6\u6001",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USERS and update.effective_user.id not in ALLOWED_USERS:
        return
    try:
        with open(DATA_FILE, encoding='utf-8') as f:
            tasks = json.load(f)
    except Exception:
        tasks = {}
    running = sum(1 for t in tasks.values() if t.get('status') == 'running')
    queued = sum(1 for t in tasks.values() if t.get('status') == 'queued')
    done = sum(1 for t in tasks.values() if t.get('status') == 'done')
    err = sum(1 for t in tasks.values() if t.get('status') == 'error')
    last = None
    for t in sorted(tasks.values(), key=lambda x: x.get('created_at', 0), reverse=True):
        if t.get('status') == 'done':
            last = t
            break
    msg = (
        f"\U0001F4CA *\u4efb\u52a1\u961f\u5217*\n"
        f"\u23f3 \u6392\u961f: {queued} | \U0001F3C3 \u8fd0\u884c: {running}\n"
        f"\u2705 \u5b8c\u6210: {done} | \u274c \u5931\u8d25: {err}\n"
    )
    if last:
        title = last.get('title', '?')[:40]
        dur = last.get('duration', 0)
        msg += f"\n\u6700\u8fd1\u5b8c\u6210: {title} ({dur}s)"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USERS and update.effective_user.id not in ALLOWED_USERS:
        return
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    yt_pattern = r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]+'
    if not re.search(yt_pattern, text):
        await update.message.reply_text("\u274c \u8bf7\u53d1\u9001 YouTube \u94fe\u63a5\uff08youtube.com/watch \u6216 youtu.be\uff09")
        return

    m = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', text)
    if not m:
        await update.message.reply_text("\u274c \u65e0\u6cd5\u8bc6\u522b\u94fe\u63a5")
        return
    url = m.group(1)

    if _start_task_callback:
        task_id = _start_task_callback(url)
        if task_id:
            await update.message.reply_text(
                f"\u2705 \u5df2\u63a5\u6536\u4efb\u52a1\n"
                f"\U0001F194 `{task_id}`\n\n"
                f"\u5f00\u59cb\u4e0b\u8f7d \u2192 \u7ffb\u8bd1 \u2192 \u4e0a\u4f20\uff0c\u5b8c\u6210\u540e\u901a\u77e5\u4f60",
                parse_mode='Markdown'
            )
            _save_chat_id(chat_id)
        else:
            await update.message.reply_text("\u274c \u521b\u5efa\u4efb\u52a1\u5931\u8d25")
    else:
        await update.message.reply_text("\u274c \u670d\u52a1\u672a\u5c31\u7eea\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5")

def _save_chat_id(chat_id):
    try:
        with open(CONFIG, encoding='utf-8') as f:
            content = f.read()
        if 'telegram_chat_id' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('telegram_chat_id:'):
                    lines[i] = f'telegram_chat_id: {chat_id}'
                    break
            content = '\n'.join(lines)
        else:
            content += f'\n# Telegram \u901a\u77e5\u76ee\u6807 chat_id\uff08bot \u81ea\u52a8\u8bb0\u5f55\uff09\ntelegram_chat_id: {chat_id}\n'
        with open(CONFIG, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception:
        pass

# === \u901a\u77e5 ===
async def send_notification_async(message, token=None, chat_id=None):
    if not token:
        token = _read_token()
    if not chat_id:
        chat_id = _read_chat_id()
    if not token or not chat_id:
        return
    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"[Telegram] \u901a\u77e5\u5931\u8d25: {e}")

def send_notification(message):
    token = _read_token()
    chat_id = _read_chat_id()
    if not token or not chat_id:
        return
    try:
        asyncio.run(send_notification_async(message, token, chat_id))
    except Exception as e:
        print(f"[Telegram] \u901a\u77e5\u53d1\u9001\u5f02\u5e38: {e}")

# === \u542f\u52a8 Bot ===
def start_bot_sync():
    """\u540c\u6b65\u542f\u52a8 Bot\uff0c\u5728\u5b50\u7ebf\u7a0b\u4e2d\u8fd0\u884c"""
    import subprocess
    token = _read_token()
    if not token:
        print("[Telegram] \u672a\u914d\u7f6e token\uff0c\u8df3\u8fc7 bot \u542f\u52a8")
        return

    # \u7528 subprocess \u542f\u52a8\u72ec\u7acb\u8fdb\u7a0b
    bot_script = os.path.join(BASE, 'telegram_bot_runner.py')
    with open(bot_script, 'w') as f:
        f.write(f'''#!/usr/bin/env python3
import asyncio, sys
sys.path.insert(0, "{BASE}")
from telegram_bot import _read_token, _read_chat_id, _save_chat_id
token = _read_token()
if not token:
    print("No token")
    sys.exit(1)

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update

async def cmd_start(update, context):
    await update.message.reply_text(
        "\\U0001F916 *yt2bili Bot*\\n\\u76f4\\u63a5\\u53d1 YouTube \\u94fe\\u63a5\\uff0c\\u81ea\\u52a8\\u4e0b\\u8f7d\\u2192\\u7ffb\\u8bd1\\u2192\\u4e0a\\u4f20 B \\u7ad9",
        parse_mode='Markdown'
    )

async def cmd_status(update, context):
    import json
    try:
        with open("{DATA_FILE}", encoding='utf-8') as f:
            tasks = json.load(f)
    except:
        tasks = {{}}
    running = sum(1 for t in tasks.values() if t.get('status') == 'running')
    queued = sum(1 for t in tasks.values() if t.get('status') == 'queued')
    done = sum(1 for t in tasks.values() if t.get('status') == 'done')
    err = sum(1 for t in tasks.values() if t.get('status') == 'error')
    await update.message.reply_text(
        f"\\U0001F4CA *\\u4efb\\u52a1\\u961f\\u5217*\\n\\u23f3 \\u6392\\u961f: {{queued}} | \\U0001F3C3 \\u8fd0\\u884c: {{running}}\\n\\u2705 \\u5b8c\\u6210: {{done}} | \\u274c \\u5931\\u8d25: {{err}}",
        parse_mode='Markdown'
    )

async def cmd_msg(update, context):
    import re
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    yt = r'(https?://(?:www\\\\.)?(?:youtube\\\\.com/watch\\\\?v=|youtu\\\\.be/)[\\\\w-]+)'
    m = re.search(yt, text)
    if not m:
        await update.message.reply_text("\\u274c \\u8bf7\\u53d1\\u9001 YouTube \\u94fe\\u63a5")
        return
    url = m.group(1)
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:5000/start",
        data=('{{"url":"' + url + '")}}').encode(),
        headers={{"Content-Type": "application/json"}},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tid = data.get('task_id', '')
        _save_chat_id(chat_id)
        await update.message.reply_text(f"\\u2705 \\u5df2\\u63a5\\u6536\\u4efb\\u52a1\\n\\U0001F194 `{{tid}}`\\n\\n\\u5f00\\u59cb\\u4e0b\\u8f7d\\u2192\\u7ffb\\u8bd1\\u2192\\u4e0a\\u4f20\\uff0c\\u5b8c\\u6210\\u540e\\u901a\\u77e5\\u4f60", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"\\u274c \\u521b\\u5efa\\u4efb\\u52a1\\u5931\\u8d25: {{e}}")

app = Application.builder().token(token).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_msg))
print("[Telegram] Bot \\u542f\\u52a8\\u5b8c\\u6210")
app.run_polling(allowed_updates=Update.ALL_TYPES)
''')
    # \u542f\u52a8\u72ec\u7acb\u8fdb\u7a0b
    proc = subprocess.Popen(
        [sys.executable, bot_script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True
    )
    print(f"[Telegram] Bot \u8fdb\u7a0b\u5df2\u542f\u52a8 (PID {proc.pid})")

def run_bot():
    """\u5728\u5b50\u7ebf\u7a0b\u4e2d\u542f\u52a8 Bot \u8fdb\u7a0b"""
    t = threading.Thread(target=start_bot_sync, daemon=True)
    t.start()
    return t