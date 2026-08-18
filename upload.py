#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传视频到 Bilibili (用调试成功的 bili_webup 方法)
用法: python3 upload.py --video <file> --cookie <cookies.json> --title <t> --desc <d> --tid <id> --tags <a,b> [--source <url>]
"""
import sys, os, json, argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', required=True)
    ap.add_argument('--cookie', required=True)
    ap.add_argument('--title', default='视频')
    ap.add_argument('--desc', default='')
    ap.add_argument('--tid', default='171')
    ap.add_argument('--tags', default='')
    ap.add_argument('--source', default='https://www.youtube.com')
    args = ap.parse_args()

    video = os.path.abspath(args.video)
    cookie = os.path.abspath(args.cookie)
    if not os.path.isfile(video):
        print(f"!! 视频文件不存在: {video}"); sys.exit(1)
    if not os.path.isfile(cookie):
        print(f"!! cookie文件不存在: {cookie}"); sys.exit(1)

    # cookie 结构预检（对齐 biliup 期望的 cookie_info/token_info 格式）
    try:
        with open(cookie, encoding='utf-8') as f:
            ck = json.load(f)
    except Exception as e:
        print(f"!! cookies.json 无法读取: {e}", file=sys.stderr)
        print("!! 请在 Web 页面「上传 Bilibili」处重新扫码登录后重试", file=sys.stderr); sys.exit(1)
    if not isinstance(ck, dict) or 'cookie_info' not in ck or 'token_info' not in ck:
        print("!! cookies.json 格式不正确: 缺少 cookie_info/token_info 字段（可能是旧版本生成）", file=sys.stderr)
        print("!! 请在 Web 页面「上传 Bilibili」处重新扫码登录后重试", file=sys.stderr); sys.exit(1)
    if not ck.get('cookie_info', {}).get('cookies'):
        print("!! cookies.json 中未找到 cookie 列表，请重新扫码登录后重试", file=sys.stderr); sys.exit(1)

    # 找 bili_webup
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '../.venv/lib/python3.11/site-packages'))
    # 尝试找 venv 路径
    for base in [os.path.dirname(os.path.realpath(__file__)), os.getcwd()]:
        sp = os.path.join(base, '.venv/lib/python3.11/site-packages')
        if os.path.isdir(sp):
            sys.path.insert(0, sp); break
    try:
        from biliup.plugins.bili_webup import BiliWeb, FileInfo
    except ImportError:
        print("!! 无法导入 biliup.plugins.bili_webup, 请确认 .venv/bin/ 安装了 biliup"); sys.exit(1)

    tags = [t.strip() for t in args.tags.split(',') if t.strip()] or ['科技']

    uploader = BiliWeb(
        principal='default',
        data={
            'name': 'default',
            'url': args.source,
            'format_title': args.title[:80],
        },
        user={'name': 'default', 'app_key': '', 'appsec': ''},
        user_cookie=cookie,
        copyright=2,
        tid=int(args.tid),
        tags=tags,
        description=args.desc[:1000],
        lines='tx',
    )

    print(f"上传: {args.title}", flush=True)
    uploader.upload(file_list=[FileInfo(video=video)])
    print("上传完成!", flush=True)

if __name__ == '__main__':
    main()