#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_bot.py - 可选的独立 Telegram 听书下载 Bot 守护进程

功能:
- 支持直接在 Telegram 中发送 /search <书名> 检索小说
- 支持发送 /download <编号或ID> [范围] (例如: /download 1 1-20) 自动下载并整体打包
- 打包完成后自动将 ZIP 压缩包或整理目录归档到指定目标路径
- 若打包文件小于 50MB (Telegram 限制)，支持直接将文件推送到 Telegram 对话

用法:
    python tg_bot.py
"""

import os
import sys
import re
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List

sys.path.append(str(Path(__file__).resolve().parent))
from audiobook_dl import Config, search_audiobooks, get_book_info, run_download, load_search_cache, save_search_cache, package_and_deliver, download_single_chapter, sanitize_filename


def send_tg_message(token: str, chat_id: int, text: str, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        sys.stderr.write(f"[TG Error] 发送消息失败: {e}\n")
        return None


def send_tg_document(token: str, chat_id: int, file_path: Path, caption: str = ""):
    """发送小于 50MB 的文件给用户"""
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    body = bytearray()
    # chat_id
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))
    # caption
    if caption:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode("utf-8"))
    # file
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    with open(file_path, "rb") as f:
        body.extend(f.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        sys.stderr.write(f"[TG Error] 发送文件失败: {e}\n")
        return None


def send_tg_audio(token: str, chat_id: int, file_path: Path, title: str = "", performer: str = "", caption: str = ""):
    """使用 Telegram sendAudio API 原生发送可直接收听的音频文件，支持聊天框即点即播"""
    url = f"https://api.telegram.org/bot{token}/sendAudio"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))
    if title:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="title"\r\n\r\n{title}\r\n'.encode("utf-8"))
    if performer:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="performer"\r\n\r\n{performer}\r\n'.encode("utf-8"))
    if caption:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode("utf-8"))

    mime = "audio/mp4" if file_path.suffix.lower() in [".m4a", ".aac"] else "audio/mpeg"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="audio"; filename="{file_path.name}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
    with open(file_path, "rb") as f:
        body.extend(f.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        sys.stderr.write(f"[TG Error] 发送音频失败: {e}\n")
        return None


def handle_search(token: str, chat_id: int, query: str, config: Config):
    send_tg_message(token, chat_id, f"🔍 正在检索《<b>{query}</b>》有声小说资源...")
    candidates = search_audiobooks(query, config, limit=5)
    if not candidates:
        send_tg_message(token, chat_id, f"❌ 未能找到与“{query}”相关的有声书，请尝试精简书名重试。")
        return

    text = f"📚 为您找到以下《<b>{query}</b>》相关版本：\n\n"
    for idx, b in enumerate(candidates, 1):
        text += f"<b>{idx}. {b['title']}</b>\n"
        text += f"   • 作者: {b['author']} | 播音: {b['broadcaster']}\n"
        text += f"   • 状态: {b['status']} | 来源: {b['source']}\n\n"

    text += "👉 <b>下载指令示例:</b>\n"
    text += "• <code>/download 1</code> （下载第1个版本的全本并打包）\n"
    text += "• <code>/download 1 1-20</code> （下载前20集并打包）\n"
    send_tg_message(token, chat_id, text)


def handle_download(token: str, chat_id: int, args_str: str, config: Config):
    parts = args_str.strip().split()
    if not parts:
        send_tg_message(token, chat_id, "⚠️ 请指定下载目标，例如: <code>/download 1</code> 或 <code>/download 11229 1-20</code>")
        return

    target = parts[0]
    start_ep = 1
    end_ep = None

    if len(parts) > 1:
        range_str = parts[1]
        m = re.match(r'(\d+)-(\d+)', range_str)
        if m:
            start_ep = int(m.group(1))
            end_ep = int(m.group(2))
        elif range_str.isdigit():
            end_ep = int(range_str)

    # 解析书籍
    cache = load_search_cache()
    book_id = target
    broadcaster = ""
    if target.isdigit() and int(target) <= len(cache):
        idx = int(target) - 1
        book_id = cache[idx]["id"]
        broadcaster = cache[idx].get("broadcaster", "")

    send_tg_message(token, chat_id, f"⏳ 正在解析书籍 <code>{book_id}</code> 目录信息...")
    info = get_book_info(book_id, config)
    if not info or not info.get("chapters"):
        send_tg_message(token, chat_id, f"❌ 无法解析该有声书目录 (ID: {book_id})")
        return

    book_title = info["title"]
    all_chapters = info["chapters"]
    total_chapters = len(all_chapters)

    s_ep = max(1, start_ep)
    e_ep = end_ep or total_chapters
    selected_chapters = [c for c in all_chapters if s_ep <= c["num"] <= e_ep]

    if not selected_chapters:
        send_tg_message(token, chat_id, f"❌ 章节区间为空 (总共 {total_chapters} 集)")
        return

    send_tg_message(
        token, chat_id,
        f"🚀 <b>开始下载《{book_title}》</b>\n"
        f"• 计划章节: 第 {s_ep} 集 至 第 {e_ep} 集 (共 {len(selected_chapters)} 集)\n"
        f"• 打包模式: {config.package_format}\n"
        f"• 目标目录: <code>{config.audiobook_dir}</code>\n\n"
        f"<i>下载中请稍候，完成后将自动发送归档通知...</i>"
    )

    # 临时目录
    final_dest_dir = Path(os.path.expanduser(config.audiobook_dir))
    temp_work_dir = final_dest_dir / ".cache_downloads" / f"{book_id}_{sanitize_filename(book_title)}"
    temp_work_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    failed = []

    for idx, ch in enumerate(selected_chapters, 1):
        ok, filepath, _ = download_single_chapter(book_id, ch, temp_work_dir, config)
        if ok and filepath:
            downloaded.append(filepath)
        else:
            failed.append(ch['num'])

    if not downloaded:
        send_tg_message(token, chat_id, "❌ 下载失败：所选章节均未能成功获取音频直链。")
        return

    # 整理至目标目录
    pkg = package_and_deliver(
        src_folder=temp_work_dir,
        book_title=book_title,
        broadcaster=broadcaster,
        dest_dir=final_dest_dir,
        package_mode=config.package_format,
        config=config,
        clean_cache=config.clean_cache_after_packaging
    )

    res_msg = (
        f"✅ <b>听书下载完成，已保存为可直接播放格式！</b>\n\n"
        f"• <b>书名</b>: 《{book_title}》\n"
        f"• <b>播音</b>: {broadcaster or '官方'}\n"
        f"• <b>集数</b>: 第 {s_ep} - {e_ep} 集 (成功 {len(downloaded)} 集)\n"
        f"• <b>体积</b>: {pkg['total_size_mb']} MB\n"
        f"• <b>格式</b>: 原生音频 (M4A/MP3，内嵌有声标签)\n"
        f"• <b>本地目录</b>: <code>{pkg['primary_path']}</code>\n"
    )
    if pkg.get("playlist_path"):
        res_msg += f"• <b>播放列表</b>: <code>{pkg['playlist_path']}</code> (双击全书连播)\n"

    send_tg_message(token, chat_id, res_msg)

    # 若配置开启了推送音频且下载集数不超过限制，才推送到聊天窗口供直接收听
    if config.send_audio_to_chat and len(downloaded) <= config.max_send_audio_episodes:
        send_tg_message(token, chat_id, "🎧 正在将音频发送至聊天窗口，支持直接点播：")
        for audio_f in downloaded:
            m = re.match(r'^\d+_(.*)$', audio_f.stem)
            ch_title = m.group(1) if m else audio_f.stem
            send_tg_audio(
                token,
                chat_id,
                audio_f,
                title=ch_title,
                performer=broadcaster,
                caption=f"《{book_title}》 {ch_title}"
            )
    elif config.package_format == "zip":
        primary_file = Path(pkg["primary_path"])
        if primary_file.is_file() and primary_file.suffix == ".zip":
            size_mb = primary_file.stat().st_size / (1024 * 1024)
            if size_mb <= config.send_file_threshold_mb:
                send_tg_message(token, chat_id, "📤 正在推送归档文件至聊天窗口...")
                send_tg_document(token, chat_id, primary_file, caption=f"《{book_title}》第 {s_ep}-{e_ep} 集归档")


def main():
    config = Config()
    token = config.telegram_bot_token
    if not token:
        print("[!] 错误: 未找到 Telegram Bot Token。请在 config.yaml 或 ~/.hermes/.env 中设置 TELEGRAM_BOT_TOKEN。")
        sys.exit(1)

    print(f"[*] 听书 Telegram Bot 守护服务启动中...")
    print(f"[*] 目标保存目录: {config.audiobook_dir}")
    print(f"[*] 打包格式: {config.package_format}")
    if config.telegram_allowed_users:
        print(f"[*] 白名单用户: {config.telegram_allowed_users}")

    last_offset = 0
    poll_url = f"https://api.telegram.org/bot{token}/getUpdates"

    while True:
        try:
            params = urllib.parse.urlencode({"offset": last_offset, "timeout": 30})
            req = urllib.request.Request(f"{poll_url}?{params}")
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode())
                updates = data.get("result", [])
                for upd in updates:
                    last_offset = max(last_offset, upd["update_id"] + 1)
                    msg = upd.get("message")
                    if not msg or "text" not in msg:
                        continue

                    chat_id = msg["chat"]["id"]
                    from_user = str(msg.get("from", {}).get("id", ""))
                    text = msg["text"].strip()

                    # 白名单验证
                    if config.telegram_allowed_users and from_user not in config.telegram_allowed_users:
                        send_tg_message(token, chat_id, "⛔ 对不起，您没有权限操作此有声书下载服务。")
                        continue

                    if text.startswith("/start") or text.startswith("/help"):
                        help_text = (
                            "👋 <b>欢迎使用有声书自动化下载助手</b>\n\n"
                            "📚 <b>支持指令:</b>\n"
                            "• <code>/search &lt;书名&gt;</code> - 搜索小说有声书\n"
                            "• <code>/download &lt;编号/ID&gt; [区间]</code> - 下载并整体打包\n\n"
                            "💡 <b>自然语言示例:</b>\n"
                            "• <code>搜听书 官道无疆</code>\n"
                            "• <code>/download 1 1-50</code> (打包下载第1个结果的前50集)\n"
                            f"\n📁 <b>打包存储路径:</b> <code>{config.audiobook_dir}</code>"
                        )
                        send_tg_message(token, chat_id, help_text)

                    elif text.startswith("/search "):
                        query = text[8:].strip()
                        handle_search(token, chat_id, query, config)

                    elif text.startswith("搜听书 "):
                        query = text[4:].strip()
                        handle_search(token, chat_id, query, config)

                    elif text.startswith("/download"):
                        args_str = text[9:].strip()
                        handle_download(token, chat_id, args_str, config)

                    elif text.startswith("下载 "):
                        args_str = text[3:].strip()
                        handle_download(token, chat_id, args_str, config)

        except Exception as e:
            time.sleep(3)


if __name__ == "__main__":
    main()
