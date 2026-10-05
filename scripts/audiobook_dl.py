#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audiobook_dl.py - 听书检索、下载与整体打包 CLI 工具 (支持 Hermes Agent 与 Telegram 工作流)

主要功能:
- 检索小说：支持按书名/作者检索恋听网等主流公开源。
- 智能直连：自动解析国内阿里 DNS 直连 IP，穿透科学上网/TUN Fake-IP 劫持，解决 TLS 握手 SSL 35 错误。
- 智能反爬：内建动态签名提取、请求退避机制与 CDN 直链解析。
- 断点续传：支持中断后继续下载，自动跳过已校验完成的章节。
- 整体打包：下载完成后将全书自动打包为 .zip 或规范专辑目录，归档至配置的指定目标目录。
- 搜索缓存：支持直接使用编号（1, 2, 3...）或书籍 ID / 链接进行快速下载。

CLI 用法:
    python audiobook_dl.py search "官道无疆" [--limit 5] [--json]
    python audiobook_dl.py info 1 [--json]
    python audiobook_dl.py download 1 [--start 1] [--end 20] [--package zip|folder|both] [--dest DIR] [--json]
"""

import os
import sys
import re
import json
import time
import shutil
import zipfile
import argparse
import subprocess
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

try:
    import yaml
except ImportError:
    yaml = None


# =====================================================================
# 配置加载类
# =====================================================================

class Config:
    def __init__(self, config_path: Optional[str] = None):
        self.audiobook_dir = str(Path.home() / "Downloads" / "Audiobooks")
        self.package_format = "folder"  # "folder" (直接可听的专辑目录), "both", "zip"
        self.embed_metadata = True
        self.generate_m3u_playlist = True
        self.clean_cache_after_packaging = True
        self.rate_limit_delay = 2.0
        self.max_retries = 5
        self.direct_ip = "47.238.53.179"
        self.auto_resolve_dns = True
        self.dns_servers = ["223.5.5.5", "119.29.29.29", "114.114.114.114"]
        self.default_max_chapters = 0
        self.telegram_bot_token = ""
        self.telegram_allowed_users = []
        self.send_audio_to_chat = False
        self.max_send_audio_episodes = 5
        self.send_file_threshold_mb = 48
        self.cookie = ""

        # 查找配置文件
        candidate_paths = []
        if config_path:
            candidate_paths.append(Path(config_path))

        script_dir = Path(__file__).resolve().parent
        candidate_paths.extend([
            Path.cwd() / "config.yaml",
            script_dir / "config.yaml",
            script_dir.parent / "config.yaml",
            Path.home() / ".hermes" / "skills" / "media" / "audiobook-download" / "config.yaml",
        ])

        for p in candidate_paths:
            if p.is_file():
                self._load_yaml(p)
                break

        # 从 ~/.hermes/.env 补充 Telegram 配置
        hermes_env = Path.home() / ".hermes" / ".env"
        if hermes_env.is_file() and not self.telegram_bot_token:
            try:
                with open(hermes_env, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TELEGRAM_BOT_TOKEN="):
                            self.telegram_bot_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("TELEGRAM_ALLOWED_USERS="):
                            raw_u = line.split("=", 1)[1].strip().strip('"').strip("'")
                            self.telegram_allowed_users = [u.strip() for u in raw_u.split(",") if u.strip()]
            except Exception:
                pass

    def _load_yaml(self, path: Path):
        if not yaml:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if data.get("audiobook_dir"):
                    self.audiobook_dir = os.path.expanduser(str(data["audiobook_dir"]))
                if data.get("package_format"):
                    self.package_format = str(data["package_format"]).lower()
                if "embed_metadata" in data:
                    self.embed_metadata = bool(data["embed_metadata"])
                if "generate_m3u_playlist" in data:
                    self.generate_m3u_playlist = bool(data["generate_m3u_playlist"])
                if "clean_cache_after_packaging" in data:
                    self.clean_cache_after_packaging = bool(data["clean_cache_after_packaging"])
                if "rate_limit_delay" in data:
                    self.rate_limit_delay = float(data["rate_limit_delay"])
                if "max_retries" in data:
                    self.max_retries = int(data["max_retries"])
                if data.get("direct_ip"):
                    self.direct_ip = str(data["direct_ip"]).strip()
                if data.get("cookie"):
                    self.cookie = str(data["cookie"]).strip()
                if "auto_resolve_dns" in data:
                    self.auto_resolve_dns = bool(data["auto_resolve_dns"])
                if data.get("dns_servers"):
                    self.dns_servers = [str(s).strip() for s in data["dns_servers"]]
                if "default_max_chapters" in data:
                    self.default_max_chapters = int(data["default_max_chapters"])
                tg = data.get("telegram") or {}
                if tg.get("bot_token"):
                    self.telegram_bot_token = str(tg["bot_token"]).strip()
                if tg.get("allowed_users"):
                    self.telegram_allowed_users = [str(u).strip() for u in tg["allowed_users"]]
                if "send_audio_to_chat" in tg:
                    self.send_audio_to_chat = bool(tg["send_audio_to_chat"])
                if "max_send_audio_episodes" in tg:
                    self.max_send_audio_episodes = int(tg["max_send_audio_episodes"])
                if "send_file_threshold_mb" in tg:
                    self.send_file_threshold_mb = int(tg["send_file_threshold_mb"])
        except Exception as e:
            sys.stderr.write(f"[WARN] 加载配置文件 {path} 失败: {e}\n")


# =====================================================================
# DNS 直连与网络请求引擎
# =====================================================================

_CACHED_REAL_IP: Optional[str] = None

def get_real_ip(config: Config) -> str:
    """自动获取 ting55.com 直连 IP，避免本地代理/TUN Fake-IP 劫持"""
    global _CACHED_REAL_IP
    if _CACHED_REAL_IP:
        return _CACHED_REAL_IP

    if not config.auto_resolve_dns:
        _CACHED_REAL_IP = config.direct_ip
        return _CACHED_REAL_IP

    for dns in config.dns_servers:
        try:
            cmd = ["dig", f"@{dns}", "ting55.com", "+short"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=4).decode().strip()
            ips = [line.strip() for line in out.splitlines() if re.match(r"^\d+\.\d+\.\d+\.\d+$", line.strip())]
            if ips:
                _CACHED_REAL_IP = ips[0]
                return _CACHED_REAL_IP
        except Exception:
            continue

    _CACHED_REAL_IP = config.direct_ip
    return _CACHED_REAL_IP


def fetch_html(url: str, config: Config, max_retries: Optional[int] = None, delay: Optional[float] = None) -> str:
    """具备退避重试与 IP 固定的 HTML 抓取"""
    real_ip = get_real_ip(config)
    retries = max_retries if max_retries is not None else config.max_retries
    base_delay = delay if delay is not None else config.rate_limit_delay

    cmd = [
        'curl', '-k', '--http1.1', '-s', '-L',
        '--resolve', f'ting55.com:443:{real_ip}',
        '--resolve', f'ting55.com:80:{real_ip}',
        '-A', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '--connect-timeout', '10',
        url
    ]

    for attempt in range(1, retries + 1):
        try:
            time.sleep(base_delay)
            res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode('utf-8', errors='ignore')
            if "<html" in res or "<head" in res:
                return res
        except subprocess.CalledProcessError:
            # 退出码 35 为 TLS 握手频繁拒绝，退避延时再试
            time.sleep(base_delay * attempt + 1.0)
        except Exception:
            time.sleep(2.0)

    return ""


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    clean = re.sub(r'[/\\:*?"<>|]', '_', name.strip())
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip() or "audiobook"


# =====================================================================
# 缓存搜索结果 (供 Telegram 对话按序号 "1, 2, 3" 触发下载)
# =====================================================================

CACHE_FILE = Path.home() / ".cache" / "hermes_audiobook_search.json"

def save_search_cache(candidates: List[Dict[str, Any]]):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_search_cache() -> List[Dict[str, Any]]:
    if not CACHE_FILE.is_file():
        return []
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# =====================================================================
# 检索与详情解析
# =====================================================================

def search_audiobooks(query: str, config: Config, limit: int = 5) -> List[Dict[str, Any]]:
    """搜索小说并返回规范化候选列表"""
    encoded_query = urllib.parse.quote(query.strip())
    search_url = f"https://ting55.com/search/{encoded_query}"
    html = fetch_html(search_url, config, delay=1.0)
    if not html:
        return []

    # 提取搜索结果列表
    books: List[Dict[str, Any]] = []
    pattern = (
        r'<li>\s*<div class=\"img\"><a href=\"/book/(\d+)\"[^>]*title=\"([^\"]+)\"[^>]*>'
        r'<img[^>]*src=\"([^\"]+)\"[^>]*alt=\"([^\"]+)\"[^>]*></a></div>\s*'
        r'<div class=\"info\"><h4[^>]*><a[^>]*>(.*?)</a></h4>\s*'
        r'<p>作者：(.*?)</p>\s*<p>播音：(.*?)</p>\s*<p>状态：(.*?)</p>\s*</div>\s*</li>'
    )

    for m in re.finditer(pattern, html, re.DOTALL):
        b_id, title_attr, cover, alt, raw_title, author, broad, status = m.groups()
        clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
        cover_url = cover if cover.startswith("http") else f"https:{cover}"
        books.append({
            "id": b_id,
            "title": clean_title,
            "author": author.strip(),
            "broadcaster": broad.strip(),
            "status": status.strip(),
            "cover_url": cover_url,
            "source": "恋听网 (Ting55)",
            "url": f"https://ting55.com/book/{b_id}",
        })
        if len(books) >= limit:
            break

    # 保存缓存
    if books:
        save_search_cache(books)

    return books


def get_book_info(book_id_or_url: str, config: Config) -> Optional[Dict[str, Any]]:
    """获取指定小说的详细信息及全书章节目录"""
    # 提取纯数字 ID
    m = re.search(r'(\d+)', str(book_id_or_url))
    if not m:
        return None
    book_id = m.group(1)

    url = f"https://ting55.com/book/{book_id}"
    html = fetch_html(url, config, max_retries=4, delay=1.5)
    if not html:
        return None

    # 解析标题
    title_m = re.search(r'<h1>([^<]+)</h1>', html)
    raw_title = title_m.group(1).strip() if title_m else f"book_{book_id}"
    clean_title = sanitize_filename(raw_title)

    # 解析简介
    desc_m = re.search(r'<div class=[\"\x27]desc[\"\x27][^>]*>(.*?)</div>', html, re.DOTALL)
    description = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""

    # 解析所有章节列表 href="/book/11229-1"
    raw_chapters = re.findall(rf'href=[\"\x27]/book/{book_id}-(\d+)[\"\x27][^>]*>([^<]+)</a>', html)
    chapters = []
    seen = set()
    for ch_num_str, ch_name in raw_chapters:
        n = int(ch_num_str)
        if n not in seen:
            seen.add(n)
            chapters.append({
                "num": n,
                "title": ch_name.strip(),
                "url": f"https://ting55.com/book/{book_id}-{n}"
            })
    chapters.sort(key=lambda x: x["num"])

    return {
        "id": book_id,
        "title": clean_title,
        "raw_title": raw_title,
        "description": description,
        "chapters_count": len(chapters),
        "chapters": chapters,
        "url": url,
    }


# =====================================================================
# 音频直链解析与单集下载
# =====================================================================

def get_meta(html: str, name: str) -> str:
    m = re.search(r'<meta\s+name=[\"\x27]' + name + r'[\"\x27]\s+content=[\"\x27]([^\x27\"]+)[\"\x27]', html)
    return m.group(1) if m else ''


def get_audio_stream_url(book_id: str, page_num: int, config: Config) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """解析章节页面并通过 /nlinka 获得最终 CDN 音频播放地址"""
    real_ip = get_real_ip(config)
    page_url = f"https://ting55.com/book/{book_id}-{page_num}"
    html = fetch_html(page_url, config, max_retries=4, delay=1.5)
    if not html:
        return None, None, "获取章节页面失败"

    b = get_meta(html, '_b') or str(book_id)
    cp = get_meta(html, '_cp') or str(page_num)
    p = get_meta(html, '_p') or '0'
    c = get_meta(html, '_c')
    l = get_meta(html, '_l') or '1'
    ext = get_meta(html, '_f') or 'm4a'

    if not c:
        return None, None, "未找到防盗链密钥(_c)"

    cmd_post = [
        'curl', '-k', '--http1.1', '-s',
        '--resolve', f'ting55.com:443:{real_ip}',
        '-A', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        '-H', f'Referer: https://ting55.com/book/{b}-{cp}',
        '-H', 'X-Requested-With: XMLHttpRequest',
        '-H', f'xt: {c}',
        '-H', f'l: {l}',
    ]
    if config.cookie:
        cmd_post.extend(['-H', f'Cookie: {config.cookie}'])
    cmd_post.extend([
        '-d', f'bookId={b}&isPay={p}&page={cp}',
        'https://ting55.com/nlinka'
    ])

    for attempt in range(1, config.max_retries + 1):
        try:
            time.sleep(config.rate_limit_delay)
            res = subprocess.check_output(cmd_post, stderr=subprocess.DEVNULL).decode('utf-8', errors='ignore')
            data = json.loads(res)
            url = data.get('ourl') or data.get('url') or data.get('plink')
            if url:
                if '.m4a' in url.lower():
                    ext = 'm4a'
                elif '.mp3' in url.lower():
                    ext = 'mp3'
                return url, ext, None
            elif data.get('status') == -1:
                return None, None, "本章节为付费/VIP章节 (需恋听网购买/提供登录Cookie)"
            elif data.get('status') == 0:
                return None, None, f"接口返回无权限或参数错误: {data}"
        except subprocess.CalledProcessError:
            time.sleep(2.5 * attempt)
        except Exception:
            time.sleep(2.0)

    return None, None, "请求 /nlinka 超过最大重试次数"


def download_single_chapter(book_id: str, chapter: Dict[str, Any], temp_dir: Path, config: Config) -> Tuple[bool, Optional[Path], int]:
    """下载单个章节，支持断点续传与大小检查"""
    page_num = chapter["num"]
    clean_title = sanitize_filename(chapter["title"])
    base_name = f"{page_num:04d}_{clean_title}"

    # 检查是否已存在有效文件
    for ext_cand in ["m4a", "mp3", "aac"]:
        target_file = temp_dir / f"{base_name}.{ext_cand}"
        if target_file.is_file() and target_file.stat().st_size > 102400:  # > 100KB 视为有效
            return True, target_file, target_file.stat().st_size

    # 尝试解析音频播放直链（最多尝试2轮）
    audio_url = None
    ext = "m4a"
    for attempt in range(1, 3):
        audio_url, ext, err = get_audio_stream_url(book_id, page_num, config)
        if audio_url:
            break
        time.sleep(2.0 * attempt)

    if not audio_url:
        return False, None, 0

    target_file = temp_dir / f"{base_name}.{ext}"
    part_file = temp_dir / f"{base_name}.{ext}.part"

    cmd_dl = [
        'curl', '-s', '-L',
        '-A', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        '-H', f'Referer: https://ting55.com/book/{book_id}-{page_num}',
        '--connect-timeout', '15',
        '--retry', '3',
        '-C', '-',  # 断点续传
        '-o', str(part_file),
        audio_url
    ]

    try:
        subprocess.run(cmd_dl, check=True)
        if part_file.is_file() and part_file.stat().st_size > 10240:
            part_file.replace(target_file)
            return True, target_file, target_file.stat().st_size
        else:
            if part_file.is_file():
                part_file.unlink()
            return False, None, 0
    except Exception:
        if part_file.is_file():
            part_file.unlink()
        return False, None, 0


# =====================================================================
# 音频元数据注入与播放列表生成
# =====================================================================

def tag_audio_file(filepath: Path, chapter_title: str, broadcaster: str, book_title: str, track_num: int, total_tracks: int):
    """为音频文件注入规范有声书 ID3/MP4 标签，确保在播放器中显示专辑、书名、主播与音轨号"""
    try:
        ext = filepath.suffix.lower()
        if ext in [".m4a", ".mp4", ".aac"]:
            from mutagen.mp4 import MP4
            tags = MP4(filepath)
            tags["\xa9nam"] = chapter_title
            if broadcaster:
                tags["\xa9ART"] = broadcaster
                tags["\xa9wrt"] = broadcaster
            tags["\xa9alb"] = book_title
            tags["\xa9gen"] = "Audiobook"
            tags["trkn"] = [(track_num, total_tracks)]
            tags.save()
        elif ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.mp3 import MP3
            try:
                audio = MP3(filepath, ID3=EasyID3)
            except Exception:
                from mutagen.id3 import ID3
                id3 = ID3()
                id3.save(filepath)
                audio = MP3(filepath, ID3=EasyID3)
            audio["title"] = chapter_title
            if broadcaster:
                audio["artist"] = broadcaster
            audio["album"] = book_title
            audio["genre"] = "Audiobook"
            audio["tracknumber"] = f"{track_num}/{total_tracks}"
            audio.save()
    except Exception:
        pass


def generate_playlist(folder: Path, book_title: str, audio_files: List[Path]) -> Optional[Path]:
    """生成 .m3u 连续播放列表文件，便于在 Mac / PC 播放器中双击全本连播"""
    playlist_path = folder / "playlist.m3u"
    try:
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write(f"#PLAYLIST:{book_title}\n\n")
            for af in audio_files:
                f.write(f"#EXTINF:-1,{af.stem}\n")
                f.write(f"{af.name}\n")
        return playlist_path
    except Exception:
        return None


# =====================================================================
# 整体整理与交付引擎 (直接可听的音频目录 / 播放列表 / 可选ZIP)
# =====================================================================

def package_and_deliver(
    src_folder: Path,
    book_title: str,
    broadcaster: str,
    dest_dir: Path,
    package_mode: str,
    config: Config,
    clean_cache: bool = True
) -> Dict[str, Any]:
    """
    将下载好的音频文件整理为直接可听的规范专辑目录，生成连播列表，并按需输出
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean_name = sanitize_filename(f"《{book_title}》" if not broadcaster or broadcaster in book_title else f"《{book_title}（{broadcaster}）》")

    folder_dest_path = dest_dir / clean_name
    zip_dest_path = dest_dir / f"{clean_name}.zip"

    created_paths = []
    total_size_bytes = 0

    # 收集有效音频文件
    audio_files = sorted([f for f in src_folder.iterdir() if f.is_file() and not f.name.endswith(".part") and not f.name.startswith(".") and f.name != "playlist.m3u"])
    total_count = len(audio_files)

    # 1. 注入有声书标签元数据
    if config.embed_metadata:
        for idx, af in enumerate(audio_files, 1):
            m = re.match(r'^\d+_(.*)$', af.stem)
            ch_title = m.group(1) if m else af.stem
            tag_audio_file(af, ch_title, broadcaster, book_title, idx, total_count)

    for f in audio_files:
        total_size_bytes += f.stat().st_size

    # 2. Folder 模式：直接保存为可原生播放的小说文件夹 (默认推荐)
    delivered_audio_files = []
    if package_mode in ["folder", "both"]:
        folder_dest_path.mkdir(parents=True, exist_ok=True)
        for audio_f in audio_files:
            target_f = folder_dest_path / audio_f.name
            if src_folder != folder_dest_path:
                shutil.copy2(audio_f, target_f)
            delivered_audio_files.append(target_f)

        # 生成连续播放列表 playlist.m3u
        if config.generate_m3u_playlist:
            generate_playlist(folder_dest_path, book_title, delivered_audio_files)

        created_paths.append(str(folder_dest_path))

    # 3. ZIP 打包模式 (仅当明确要求 zip 时启用)
    if package_mode in ["zip", "both"]:
        with zipfile.ZipFile(zip_dest_path, "w") as zf:
            for audio_f in audio_files:
                arcname = f"{clean_name}/{audio_f.name}"
                zinfo = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
                zinfo.flag_bits |= 0x800  # UTF-8 编码标识
                zinfo.compress_type = zipfile.ZIP_STORED
                with open(audio_f, "rb") as af:
                    zf.writestr(zinfo, af.read())
        created_paths.append(str(zip_dest_path))

    # 4. 清理临时下载目录
    if clean_cache and src_folder != folder_dest_path:
        try:
            shutil.rmtree(src_folder, ignore_errors=True)
        except Exception:
            pass

    size_mb = round(total_size_bytes / (1024 * 1024), 2)
    primary_path = str(folder_dest_path) if package_mode in ["folder", "both"] else str(zip_dest_path)

    return {
        "package_mode": package_mode,
        "clean_name": clean_name,
        "paths": created_paths,
        "primary_path": primary_path,
        "folder_path": str(folder_dest_path) if package_mode in ["folder", "both"] else None,
        "playlist_path": str(folder_dest_path / "playlist.m3u") if (package_mode in ["folder", "both"] and config.generate_m3u_playlist) else None,
        "total_files": len(audio_files),
        "total_size_mb": size_mb,
        "dest_dir": str(dest_dir)
    }


# =====================================================================
# CLI 命令处理逻辑
# =====================================================================

def run_search(query: str, limit: int = 5, as_json: bool = False, config_path: Optional[str] = None):
    config = Config(config_path)
    candidates = search_audiobooks(query, config, limit=limit)
    if as_json:
        print(json.dumps(candidates, ensure_ascii=False, indent=2))
        return

    if not candidates:
        print(f"[-] 未找到包含关键词 “{query}” 的有声书，请更换书名或作者重新搜索。")
        return

    print(f"\n📚 为您找到以下《{query}》相关有声书版本（输入编号即可直接下载）：\n")
    for idx, b in enumerate(candidates, 1):
        print(f"{idx}. {b['title']}")
        print(f"   • 作者: {b['author']} | 播音/演播: {b['broadcaster']} | 状态: {b['status']}")
        print(f"   • 来源: {b['source']} (ID: {b['id']})")
        print(f"   • 链接: {b['url']}")
        print()
    print("👉 回复或执行: python audiobook_dl.py download 1 开始下载全本或指定章节\n")


def run_info(id_or_index: str, as_json: bool = False, config_path: Optional[str] = None):
    config = Config(config_path)
    # 检查是否为缓存数字序号
    book_id = id_or_index
    if id_or_index.isdigit() and int(id_or_index) <= 20:
        cache = load_search_cache()
        idx = int(id_or_index) - 1
        if 0 <= idx < len(cache):
            book_id = cache[idx]["id"]

    info = get_book_info(book_id, config)
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if not info:
        print(f"[-] 获取有声书详情失败: {id_or_index}")
        return

    print(f"\n📖 书名: {info['title']}")
    print(f"• ID: {info['id']}")
    print(f"• 总集数: {info['chapters_count']} 集")
    print(f"• 简介: {info['description'][:150]}...")
    print(f"• 目录前 5 集预览:")
    for ch in info['chapters'][:5]:
        print(f"   - 第 {ch['num']} 集: {ch['title']}")
    print()


def run_download(
    target: str,
    start_ep: Optional[int] = None,
    end_ep: Optional[int] = None,
    limit: Optional[int] = None,
    package_mode: Optional[str] = None,
    dest_dir: Optional[str] = None,
    as_json: bool = False,
    config_path: Optional[str] = None
):
    config = Config(config_path)

    # 1. 解析目标书籍 (判断是否为数字序号 1, 2, 3...)
    broadcaster = ""
    book_id = target
    if target.isdigit() and int(target) <= 30:
        cache = load_search_cache()
        idx = int(target) - 1
        if 0 <= idx < len(cache):
            book_id = cache[idx]["id"]
            broadcaster = cache[idx].get("broadcaster", "")

    # 2. 获取书籍全量信息
    info = get_book_info(book_id, config)
    if not info or not info.get("chapters"):
        err = {"status": "error", "error": f"无法获取有声书 (ID: {book_id}) 的目录或信息"}
        if as_json:
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"[-] {err['error']}")
        return

    book_title = info["title"]
    all_chapters = info["chapters"]
    total_chapters = len(all_chapters)

    # 3. 确定章节下载区间
    s_ep = max(1, start_ep or 1)
    e_ep = end_ep or (s_ep + limit - 1 if limit else total_chapters)
    if config.default_max_chapters > 0 and end_ep is None and limit is None:
        e_ep = min(e_ep, s_ep + config.default_max_chapters - 1)

    selected_chapters = [c for c in all_chapters if s_ep <= c["num"] <= e_ep]
    if not selected_chapters:
        err = {"status": "error", "error": f"选定章节区间为空 (第 {s_ep} 至 {e_ep} 集，全书共 {total_chapters} 集)"}
        if as_json:
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"[-] {err['error']}")
        return

    # 4. 准备临时下载目录与最终目标目录
    final_dest_dir = Path(os.path.expanduser(dest_dir or config.audiobook_dir))
    temp_work_dir = final_dest_dir / ".cache_downloads" / f"{book_id}_{sanitize_filename(book_title)}"
    temp_work_dir.mkdir(parents=True, exist_ok=True)

    actual_package_mode = (package_mode or config.package_format).lower()

    if not as_json:
        print(f"\n==================================================")
        print(f"🚀 开始下载有声书: 《{book_title}》 (ID: {book_id})")
        print(f"• 计划章节: 第 {s_ep} 集 至 第 {e_ep} 集 (共计 {len(selected_chapters)} 集)")
        print(f"• 目标目录: {final_dest_dir}")
        print(f"• 打包模式: {actual_package_mode}")
        print(f"==================================================\n")

    # 5. 循环下载章节
    downloaded_files = []
    failed_chapters = []

    for idx, ch in enumerate(selected_chapters, 1):
        if not as_json:
            print(f"[{idx}/{len(selected_chapters)}] 正在处理 第 {ch['num']} 集: {ch['title']} ...")
        ok, filepath, size_bytes = download_single_chapter(book_id, ch, temp_work_dir, config)
        if ok and filepath:
            downloaded_files.append(filepath)
            if not as_json:
                size_mb = size_bytes / (1024 * 1024)
                print(f"    -> [成功] {filepath.name} ({size_mb:.1f} MB)")
        else:
            failed_chapters.append(ch['num'])
            if not as_json:
                print(f"    -> [失败] 第 {ch['num']} 集无法下载")

    if not downloaded_files:
        err = {"status": "error", "error": "所选章节均未能成功下载音频源"}
        if as_json:
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"\n[-] {err['error']}")
        return

    # 6. 整理至目标目录 (默认 folder 模式，直接可听)
    if not as_json:
        if actual_package_mode == "zip":
            print(f"\n📦 正在将已下载的 {len(downloaded_files)} 个音频章节打包为 ZIP 归档...")
        else:
            print(f"\n🎵 正在将已下载的 {len(downloaded_files)} 个音频文件整理为可直接播放的专辑目录...")

    package_res = package_and_deliver(
        src_folder=temp_work_dir,
        book_title=book_title,
        broadcaster=broadcaster,
        dest_dir=final_dest_dir,
        package_mode=actual_package_mode,
        config=config,
        clean_cache=config.clean_cache_after_packaging
    )

    result_payload = {
        "status": "success",
        "book_id": book_id,
        "book_title": book_title,
        "broadcaster": broadcaster,
        "start_chapter": s_ep,
        "end_chapter": e_ep,
        "total_chapters_downloaded": len(downloaded_files),
        "failed_chapters_count": len(failed_chapters),
        "package_format": actual_package_mode,
        "primary_path": package_res["primary_path"],
        "folder_path": package_res.get("folder_path"),
        "playlist_path": package_res.get("playlist_path"),
        "all_paths": package_res["paths"],
        "total_size_mb": package_res["total_size_mb"],
        "dest_dir": str(final_dest_dir),
    }

    if as_json:
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
    else:
        print(f"\n" + "=" * 50)
        print(f"✅ 有声书下载完成，已保存为可直接播放格式！")
        print(f"• 书名: 《{book_title}》")
        if broadcaster:
            print(f"• 演播: {broadcaster}")
        print(f"• 章节: 第 {s_ep} 集 至 第 {e_ep} 集 (成功: {len(downloaded_files)} 集, 失败: {len(failed_chapters)} 集)")
        print(f"• 总计体积: {package_res['total_size_mb']} MB")
        print(f"• 交付格式: 直接可听音频 (M4A/MP3 规范目录)")
        print(f"• 音频目录: {package_res['primary_path']}")
        if package_res.get("playlist_path"):
            print(f"• 连播列表: {package_res['playlist_path']} (双击即可连续播放)")
        print(f"=" * 50 + "\n")


# =====================================================================
# Main 主入口
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Hermes 有声书检索、下载与整体打包工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. 搜索
    search_parser = subparsers.add_parser("search", help="搜索有声书")
    search_parser.add_argument("query", help="小说书名或作者 (如 '官道无疆')")
    search_parser.add_argument("--limit", type=int, default=5, help="最多返回候选数 (默认 5)")
    search_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    search_parser.add_argument("--config", help="指定配置文件路径")

    # 2. 详情
    info_parser = subparsers.add_parser("info", help="查看有声书详情与章节")
    info_parser.add_argument("target", help="书籍ID、链接或上一次搜索的序号 (如 '1' 或 '11229')")
    info_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    info_parser.add_argument("--config", help="指定配置文件路径")

    # 3. 下载与打包
    dl_parser = subparsers.add_parser("download", help="下载并整体打包有声书")
    dl_parser.add_argument("target", help="书籍ID、链接或上一次搜索的序号 (如 '1' 或 '11229')")
    dl_parser.add_argument("--start", type=int, default=1, help="起始集数 (默认 1)")
    dl_parser.add_argument("--end", type=int, default=None, help="结束集数 (默认最后一集)")
    dl_parser.add_argument("--limit", type=int, default=None, help="最多下载多少集")
    dl_parser.add_argument("--package", choices=["zip", "folder", "both"], help="打包格式: zip, folder 或 both")
    dl_parser.add_argument("--dest", help="自定义保存的目标目录 (覆盖配置文件)")
    dl_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    dl_parser.add_argument("--config", help="指定配置文件路径")

    args = parser.parse_args()

    if args.command == "search":
        run_search(args.query, limit=args.limit, as_json=args.json, config_path=args.config)
    elif args.command == "info":
        run_info(args.target, as_json=args.json, config_path=args.config)
    elif args.command == "download":
        run_download(
            target=args.target,
            start_ep=args.start,
            end_ep=args.end,
            limit=args.limit,
            package_mode=args.package,
            dest_dir=args.dest,
            as_json=args.json,
            config_path=args.config
        )


if __name__ == '__main__':
    main()
