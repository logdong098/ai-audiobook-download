#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_bot.py - 可选的独立 Telegram 听书下载 Bot 守护进程 (支持喜马拉雅、B站、恋听网多源)

功能:
- 支持直接在 Telegram 中发送 /search <书名> 聚合检索喜马拉雅、B站广播剧、恋听网
- 支持发送 /download <编号或ID> [范围] (例如: /download 1 1-20) 自动下载并整理
- 下载完成后自动整理为直接可听的规范专辑目录（.m4a/.mp3 + playlist.m3u）
- 支持直接在 Telegram 聊天窗口推送原生音频流播放

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
from audiobook_dl import (
    Config,
    search_audiobooks,
    get_book_info,
    resolve_target,
    get_ximalaya_tracks_range,
    load_search_cache,
    save_search_cache,
    package_and_deliver,
    download_single_chapter,
    sanitize_filename
)


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
    """发送小于 50MB 的归档文件给用户"""
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))
    if caption:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode("utf-8"))
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
    send_tg_message(token, chat_id, f"🔍 正在跨平台（喜马拉雅/B站/恋听网）检索《<b>{query}</b>》有声小说资源...")
    candidates = search_audiobooks(query, config, limit=5)
    if not candidates:
        send_tg_message(token, chat_id, f"❌ 未能找到与“{query}”相关的有声书，请尝试更换书名关键词重试。")
        return

    source_icons = {
        "ximalaya": "🎧 [喜马拉雅]",
        "bilibili": "📺 [B站/广播剧]",
        "ting55": "📻 [恋听网]"
    }

    text = f"📚 为您找到以下《<b>{query}</b>》相关版本（回复编号即可下载）：\n\n"
    for idx, b in enumerate(candidates, 1):
        src_tag = source_icons.get(b.get("source", ""), f"[{b.get('source_name', '平台')}]")
        text += f"<b>{idx}. {b['title']}</b> {src_tag}\n"
        text += f"   • 播音/演播: {b['broadcaster']} | 状态: {b['status']}\n"
        text += f"   • 标识: <code>{b['id']}</code>\n\n"

    text += "👉 <b>下载指令示例:</b>\n"
    text += "• <code>/download 1</code> （下载第1个版本的全本）\n"
    text += "• <code>/download 1 1-20</code> （下载前20集并整理为即听目录）\n"
    send_tg_message(token, chat_id, text)


def handle_download(token: str, chat_id: int, args_str: str, config: Config):
    parts = args_str.strip().split()
    if not parts:
        send_tg_message(token, chat_id, "⚠️ 请指定下载目标，例如: <code>/download 1</code> 或 <code>/download 1 1-20</code>")
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

    source, source_id, cached = resolve_target(target)

    send_tg_message(token, chat_id, f"⏳ 正在解析书籍 <code>{target}</code> 目录与音频源...")
    info = get_book_info(target, config)
    if not info:
        send_tg_message(token, chat_id, f"❌ 无法解析该有声书目录 (标识: {target})")
        return

    book_title = info["title"]
    broadcaster = info.get("broadcaster", "")
    total_chapters = info.get("chapters_count", 0)

    s_ep = max(1, start_ep)
    e_ep = end_ep or total_chapters
    if config.default_max_chapters > 0 and end_ep is None:
        e_ep = min(e_ep, s_ep + config.default_max_chapters - 1)

    if total_chapters > 0 and e_ep > total_chapters:
        e_ep = total_chapters

    # 准备章节清单
    if source == "ximalaya":
        selected_chapters = get_ximalaya_tracks_range(source_id, s_ep, e_ep)
    else:
        all_chapters = info.get("chapters", [])
        selected_chapters = [c for c in all_chapters if s_ep <= c["num"] <= e_ep]

    if not selected_chapters:
        send_tg_message(token, chat_id, f"❌ 章节区间为空 (总共 {total_chapters} 集)")
        return

    src_name = info.get("source_name", source)
    send_tg_message(
        token, chat_id,
        f"🚀 <b>开始下载《{book_title}》</b>\n"
        f"• 来源平台: {src_name} (标识: <code>{info['id']}</code>)\n"
        f"• 演播/主播: {broadcaster or '官方'}\n"
        f"• 计划章节: 第 {s_ep} 集 至 第 {e_ep} 集 (共 {len(selected_chapters)} 集)\n"
        f"• 交付格式: 直接可听规范专辑目录 (M4A + playlist.m3u)\n"
        f"• 目标路径: <code>{config.audiobook_dir}</code>\n\n"
        f"<i>下载整理中请稍候，完成后将发送即听通知...</i>"
    )

    # 临时目录
    final_dest_dir = Path(os.path.expanduser(config.audiobook_dir))
    temp_work_dir = final_dest_dir / ".cache_downloads" / f"{source}_{source_id}_{sanitize_filename(book_title)}"
    temp_work_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    failed = []

    for idx, ch in enumerate(selected_chapters, 1):
        ok, filepath, _ = download_single_chapter(source, source_id, ch, temp_work_dir, config)
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
        f"✅ <b>听书下载完成，已整理为直接可听格式！</b>\n\n"
        f"• <b>书名</b>: 《{book_title}》\n"
        f"• <b>平台</b>: {src_name}\n"
        f"• <b>播音</b>: {broadcaster or '官方'}\n"
        f"• <b>集数</b>: 第 {s_ep} - {e_ep} 集 (成功 {len(downloaded)} 集)\n"
        f"• <b>体积</b>: {pkg['total_size_mb']} MB\n"
        f"• <b>格式</b>: 原生音频 (M4A，内嵌专辑/艺术家有声标签)\n"
        f"• <b>本地目录</b>: <code>{pkg['primary_path']}</code>\n"
    )
    if pkg.get("playlist_path"):
        res_msg += f"• <b>连播列表</b>: <code>{pkg['playlist_path']}</code> (双击全书连续播放)\n"

    send_tg_message(token, chat_id, res_msg)

    # 若配置开启了推送音频且下载集数在限制内，直接推送到 Telegram 原生音频播放器
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

    print(f"[*] 多源听书 Telegram Bot 守护服务启动中...")
    print(f"[*] 启用平台: 喜马拉雅={config.enable_ximalaya}, B站={config.enable_bilibili}, 恋听网={config.enable_ting55}")
    print(f"[*] 目标保存目录: {config.audiobook_dir}")
    print(f"[*] 交付模式: {config.package_format} (直接可听专辑目录)")
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
                            "👋 <b>欢迎使用多源有声书下载助手</b>\n\n"
                            "🌟 <b>平台聚合:</b> 喜马拉雅 FM、哔哩哔哩 (B站)、恋听网\n"
                            "📚 <b>支持指令:</b>\n"
                            "• <code>/search &lt;书名&gt;</code> - 跨平台聚合检索小说有声资源\n"
                            "• <code>/download &lt;编号/ID&gt; [区间]</code> - 下载并整理为即听目录\n\n"
                            "💡 <b>使用示例:</b>\n"
                            "• <code>/search 官道无疆</code>\n"
                            "• <code>/download 1 1-20</code> (下载第1个结果的前20集)\n"
                            "• <code>/download xm_49866738 1-10</code> (直接下载喜马拉雅专辑)\n"
                            "• <code>/download BV125SmYkEew 1-5</code> (直接下载B站广播剧分P)\n\n"
                            f"📁 <b>保存路径:</b> <code>{config.audiobook_dir}</code>\n"
                            "🎵 <b>交付格式:</b> 直接可听原生音频文件夹 + playlist.m3u"
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
