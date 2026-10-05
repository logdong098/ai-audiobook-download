#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audiobook_dl.py - 多源听书检索、下载与直接可听整理 CLI 工具
支持平台:
  1. 喜马拉雅 (Ximalaya FM) - 官方/主播高保真有声小说 (免转码直接解析 M4A 直链)
  2. 哔哩哔哩 (Bilibili B站) - 整本长篇广播剧、分P多人有声剧 (yt-dlp 高保真流提取)
  3. 恋听网 (Ting55) - 经典网络小说听书源 (国内 DNS 直连 IP 穿透与防盗链解析)

核心特性:
- 多源并发搜索：一次指令同时聚合喜马拉雅、B站、恋听网优质资源，标注来源与集数。
- 交付即听：下载完成后自动整理为规范有声书专辑文件夹，注入 ID3/MP4 元数据标签并生成 playlist.m3u 连播列表。
- 断点续传与缓存：支持自动跳过已下载章节，支持直接输入编号（1, 2, 3...）极速下载。
- 完全适配 Hermes Agent Telegram 交互工作流。

CLI 用法:
    python audiobook_dl.py search "官道无疆" [--source all|xm|bili|ting55] [--limit 5]
    python audiobook_dl.py info 1
    python audiobook_dl.py download 1 --start 1 --end 20 [--dest DIR] [--package folder|both|zip]
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
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yaml
except ImportError:
    yaml = None

try:
    import requests
    import urllib3
    urllib3.disable_warnings()
except ImportError:
    requests = None


# =====================================================================
# 配置类
# =====================================================================

class Config:
    def __init__(self, config_path: Optional[str] = None):
        self.audiobook_dir = str(Path.home() / "Downloads" / "Audiobooks")
        self.package_format = "folder"  # "folder", "both", "zip"
        self.embed_metadata = True
        self.generate_m3u_playlist = True
        self.clean_cache_after_packaging = True
        self.rate_limit_delay = 1.5
        self.max_retries = 4
        self.direct_ip = "47.238.53.179"
        self.auto_resolve_dns = True
        self.dns_servers = ["223.5.5.5", "119.29.29.29", "114.114.114.114"]
        self.default_max_chapters = 0
        self.cookie = ""

        # 多源开关与配置
        self.enable_ximalaya = True
        self.enable_bilibili = True
        self.enable_ting55 = True
        self.default_search_source = "all"
        self.yt_dlp_path = shutil.which("yt-dlp") or "/opt/miniconda3/bin/yt-dlp"

        # Telegram 配置
        self.telegram_bot_token = ""
        self.telegram_allowed_users = []
        self.send_audio_to_chat = False
        self.max_send_audio_episodes = 5
        self.send_file_threshold_mb = 48

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

                # 多源配置
                srcs = data.get("sources") or {}
                if "ximalaya" in srcs:
                    self.enable_ximalaya = bool(srcs["ximalaya"])
                if "bilibili" in srcs:
                    self.enable_bilibili = bool(srcs["bilibili"])
                if "ting55" in srcs:
                    self.enable_ting55 = bool(srcs["ting55"])
                if data.get("default_search_source"):
                    self.default_search_source = str(data["default_search_source"]).lower()
                if data.get("yt_dlp_path"):
                    self.yt_dlp_path = os.path.expanduser(str(data["yt_dlp_path"]))

                # Telegram 配置
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
# 工具函数与搜索缓存
# =====================================================================

CACHE_FILE = Path.home() / ".cache" / "hermes_audiobook_search.json"

def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    clean = re.sub(r'[/\\:*?"<>|]', '_', name.strip())
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip() or "audiobook"


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


def resolve_target(target: str) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """
    解析用户传入的目标标识 (序号 1/2/3、完整链接或 ID)
    返回: (source, source_id, cached_item)
    source 可选: 'ximalaya', 'bilibili', 'ting55'
    """
    target = target.strip()

    # 1. 纯数字且在缓存范围内
    if target.isdigit() and int(target) <= 50:
        cache = load_search_cache()
        idx = int(target) - 1
        if 0 <= idx < len(cache):
            item = cache[idx]
            return item.get("source", "ximalaya"), str(item.get("source_id") or item.get("id")), item

    # 2. 带有 source 前缀
    if target.startswith("xm_"):
        return "ximalaya", target[3:], None
    if target.startswith("bili_"):
        return "bilibili", target[5:], None
    if target.startswith("ting_"):
        return "ting55", target[5:], None

    # 3. 常见 URL 匹配
    if "bilibili.com" in target:
        m = re.search(r'(BV[a-zA-Z0-9]+)', target)
        if m:
            return "bilibili", m.group(1), None
        m_av = re.search(r'av(\d+)', target)
        if m_av:
            return "bilibili", f"av{m_av.group(1)}", None

    if "ximalaya.com" in target:
        m = re.search(r'album/(\d+)', target)
        if m:
            return "ximalaya", m.group(1), None

    if "ting55.com" in target:
        m = re.search(r'book/(\d+)', target)
        if m:
            return "ting55", m.group(1), None

    # 4. BV 号直接判定为 B 站
    if target.startswith("BV") or target.startswith("bv"):
        return "bilibili", target, None

    # 5. 纯数字 ID，根据长度经验判断 (Ting55 常为 4-5 位，喜马拉雅为 7-9 位)
    if target.isdigit():
        if len(target) >= 7:
            return "ximalaya", target, None
        else:
            return "ting55", target, None

    return "ting55", target, None


# =====================================================================
# 1. 喜马拉雅 (Ximalaya) 引擎
# =====================================================================

def search_ximalaya(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """检索喜马拉雅公开有声专辑"""
    results = []
    try:
        url = "https://search.ximalaya.com/front/v1"
        params = {
            "core": "album",
            "kw": query.strip(),
            "page": 1,
            "rows": max(limit, 5),
            "device": "iPhone",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15",
            "Referer": "https://m.ximalaya.com/",
        }
        if requests:
            r = requests.get(url, params=params, headers=headers, verify=False, timeout=6)
            data = r.json() if r.status_code == 200 else {}
        else:
            p_str = urllib.parse.urlencode(params)
            cmd = ['curl', '-s', '-k', f"{url}?{p_str}"]
            raw = subprocess.check_output(cmd, timeout=6).decode('utf-8', errors='ignore')
            data = json.loads(raw)

        docs = data.get("response", {}).get("docs", [])
        for d in docs[:limit]:
            aid = str(d.get("id"))
            is_paid = d.get("is_paid", False)
            tracks = d.get("tracks", 0)
            broadcaster = d.get("nickname", "").strip() or "喜马拉雅主播"
            title = sanitize_filename(re.sub(r'<[^>]+>', '', d.get("title", "")).strip())
            results.append({
                "id": f"xm_{aid}",
                "source_id": aid,
                "source": "ximalaya",
                "source_name": "喜马拉雅",
                "title": title,
                "author": broadcaster,
                "broadcaster": broadcaster,
                "status": f"{'付费VIP' if is_paid else '免费完结/连载'} (全书 {tracks} 集)",
                "chapters_count": tracks,
                "is_paid": is_paid,
                "cover_url": d.get("cover_path", ""),
                "url": f"https://www.ximalaya.com/album/{aid}",
            })
    except Exception as e:
        sys.stderr.write(f"[WARN] 喜马拉雅搜索出错: {e}\n")
    return results


def get_ximalaya_info(album_id: str, config: Config) -> Optional[Dict[str, Any]]:
    """获取喜马拉雅专辑详情与章节总数"""
    try:
        url = f"https://m.ximalaya.com/m-revision/page/album/v2/queryAlbumPage/{album_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)",
            "Referer": "https://m.ximalaya.com/",
        }
        if requests:
            r = requests.get(url, headers=headers, verify=False, timeout=8)
            data = r.json() if r.status_code == 200 else {}
        else:
            cmd = ['curl', '-s', '-k', '-A', headers['User-Agent'], url]
            raw = subprocess.check_output(cmd, timeout=8).decode('utf-8', errors='ignore')
            data = json.loads(raw)

        if data.get("ret") != 0:
            return None

        alb_data = data.get("data", {})
        detail = alb_data.get("albumDetailInfo", {})
        info = detail.get("albumInfo", {})
        title = sanitize_filename(info.get("title") or f"album_{album_id}")
        broadcaster = alb_data.get("albumPageMainInfo", {}).get("anchorName") or detail.get("anchorInfo", {}).get("nickname") or "有声演播"
        desc = info.get("shortIntro") or alb_data.get("albumRichInfo", {}).get("richIntro") or ""
        desc_clean = re.sub(r'<[^>]+>', '', desc).strip()
        tracks_count = detail.get("statCountInfo", {}).get("trackCount", 0)

        # 获取第 1 页作为前置预览
        preview_tracks = get_ximalaya_tracks_range(album_id, 1, min(10, tracks_count or 10))

        return {
            "id": f"xm_{album_id}",
            "source_id": album_id,
            "source": "ximalaya",
            "source_name": "喜马拉雅",
            "title": title,
            "raw_title": info.get("title", title),
            "broadcaster": broadcaster,
            "description": desc_clean,
            "chapters_count": tracks_count or len(preview_tracks),
            "chapters": preview_tracks,
            "url": f"https://www.ximalaya.com/album/{album_id}",
        }
    except Exception as e:
        sys.stderr.write(f"[WARN] 获取喜马拉雅专辑详情失败: {e}\n")
        return None


def get_ximalaya_tracks_range(album_id: str, start_ep: int, end_ep: int) -> List[Dict[str, Any]]:
    """按区间高效分页拉取喜马拉雅章节列表 (每页 30 首)"""
    start_page = (start_ep - 1) // 30 + 1
    end_page = (end_ep - 1) // 30 + 1
    tracks_by_num = {}

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Referer": f"https://www.ximalaya.com/album/{album_id}",
    }

    for p in range(start_page, end_page + 1):
        url = f"https://www.ximalaya.com/revision/album/getTracksList?albumId={album_id}&pageNum={p}"
        try:
            if requests:
                r = requests.get(url, headers=headers, verify=False, timeout=6)
                data = r.json() if r.status_code == 200 else {}
            else:
                cmd = ['curl', '-s', '-k', '-A', headers['User-Agent'], url]
                raw = subprocess.check_output(cmd, timeout=6).decode('utf-8', errors='ignore')
                data = json.loads(raw)

            tracks = data.get("data", {}).get("tracks", [])
            for idx, t in enumerate(tracks):
                ep_num = (p - 1) * 30 + idx + 1
                if start_ep <= ep_num <= end_ep:
                    tracks_by_num[ep_num] = {
                        "num": ep_num,
                        "title": sanitize_filename(t.get("title") or f"第{ep_num}集"),
                        "track_id": t.get("trackId"),
                        "duration": t.get("duration", 0),
                        "is_paid": t.get("isPaid", False),
                        "url": f"https://www.ximalaya.com/sound/{t.get('trackId')}",
                    }
        except Exception:
            continue

    return [tracks_by_num[k] for k in sorted(tracks_by_num.keys())]


def download_ximalaya_chapter(album_id: str, chapter: Dict[str, Any], temp_dir: Path, config: Config) -> Tuple[bool, Optional[Path], int]:
    """下载喜马拉雅单个音频章节"""
    ep_num = chapter["num"]
    clean_title = sanitize_filename(chapter["title"])
    base_name = f"{ep_num:04d}_{clean_title}"
    target_file = temp_dir / f"{base_name}.m4a"
    part_file = temp_dir / f"{base_name}.m4a.part"

    # 断点续传检查
    if target_file.is_file() and target_file.stat().st_size > 102400:
        return True, target_file, target_file.stat().st_size

    track_id = chapter.get("track_id")
    if not track_id:
        # 补抓 track_id
        tracks = get_ximalaya_tracks_range(album_id, ep_num, ep_num)
        if tracks:
            track_id = tracks[0].get("track_id")
    if not track_id:
        return False, None, 0

    # 获取 CDN 直链
    audio_url = None
    try:
        t_url = f"http://m.ximalaya.com/tracks/{track_id}.json"
        headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)"}
        if requests:
            r = requests.get(t_url, headers=headers, timeout=6)
            d = r.json() if r.status_code == 200 else {}
        else:
            cmd = ['curl', '-s', t_url]
            raw = subprocess.check_output(cmd, timeout=6).decode('utf-8', errors='ignore')
            d = json.loads(raw)

        if d.get("is_paid") and not d.get("play_path_64"):
            sys.stderr.write(f"[INFO] 喜马拉雅第 {ep_num} 节为付费VIP章节，跳过\n")
            return False, None, 0

        audio_url = d.get("play_path_64") or d.get("play_path") or d.get("play_path_32")
    except Exception as e:
        sys.stderr.write(f"[WARN] 解析喜马拉雅声音 {track_id} 地址失败: {e}\n")
        return False, None, 0

    if not audio_url:
        return False, None, 0

    # 执行断点续传下载
    cmd_dl = [
        'curl', '-s', '-L',
        '-A', 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)',
        '--connect-timeout', '15',
        '--retry', '3',
        '-C', '-',
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
# 2. 哔哩哔哩 (Bilibili) 引擎
# =====================================================================

def search_bilibili(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """检索 Bilibili 广播剧与长篇有声小说"""
    results = []
    try:
        url = "https://api.bilibili.com/x/web-interface/wbi/search/all/v2"
        params = {"keyword": query.strip()}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com",
        }
        if requests:
            r = requests.get(url, params=params, headers=headers, timeout=6)
            data = r.json() if r.status_code == 200 else {}
        else:
            p_str = urllib.parse.urlencode(params)
            cmd = ['curl', '-s', '-A', headers['User-Agent'], '-H', 'Referer: https://www.bilibili.com', f"{url}?{p_str}"]
            raw = subprocess.check_output(cmd, timeout=6).decode('utf-8', errors='ignore')
            data = json.loads(raw)

        for sec in data.get("data", {}).get("result", []):
            if sec.get("result_type") == "video":
                for item in sec.get("data", [])[:limit]:
                    bvid = item.get("bvid")
                    clean_title = sanitize_filename(re.sub(r'<[^>]+>', '', item.get("title", "")).strip())
                    author = item.get("author", "").strip() or "B站UP主"
                    play = item.get("play", 0)
                    play_str = f"{play/10000:.1f}万" if play >= 10000 else str(play)
                    duration = item.get("duration", "")
                    results.append({
                        "id": f"bili_{bvid}",
                        "source_id": bvid,
                        "source": "bilibili",
                        "source_name": "B站/广播剧",
                        "title": clean_title,
                        "author": author,
                        "broadcaster": author,
                        "status": f"播放: {play_str} | 时长: {duration}",
                        "chapters_count": 0,
                        "cover_url": "https:" + item.get("pic") if item.get("pic", "").startswith("//") else item.get("pic", ""),
                        "url": f"https://www.bilibili.com/video/{bvid}",
                    })
                break
    except Exception as e:
        sys.stderr.write(f"[WARN] B站搜索出错: {e}\n")
    return results


def get_bilibili_info(bvid: str, config: Config) -> Optional[Dict[str, Any]]:
    """获取 B站 视频的分 P 选集信息"""
    try:
        url = "https://api.bilibili.com/x/web-interface/view"
        params = {"bvid": bvid}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com",
        }
        if requests:
            r = requests.get(url, params=params, headers=headers, timeout=8)
            data = r.json() if r.status_code == 200 else {}
        else:
            cmd = ['curl', '-s', '-A', headers['User-Agent'], f"{url}?bvid={bvid}"]
            raw = subprocess.check_output(cmd, timeout=8).decode('utf-8', errors='ignore')
            data = json.loads(raw)

        v_data = data.get("data", {})
        if not v_data:
            return None

        title = sanitize_filename(v_data.get("title") or bvid)
        author = v_data.get("owner", {}).get("name") or "B站UP主"
        desc = v_data.get("desc") or ""

        pages = v_data.get("pages", [])
        chapters = []
        for p in pages:
            p_num = p.get("page", 1)
            p_title = sanitize_filename(p.get("part") or f"第{p_num}集")
            chapters.append({
                "num": p_num,
                "title": p_title,
                "cid": p.get("cid"),
                "duration": p.get("duration", 0),
                "url": f"https://www.bilibili.com/video/{bvid}?p={p_num}",
            })

        return {
            "id": f"bili_{bvid}",
            "source_id": bvid,
            "source": "bilibili",
            "source_name": "B站/广播剧",
            "title": title,
            "raw_title": v_data.get("title", title),
            "broadcaster": author,
            "description": desc[:300],
            "chapters_count": len(chapters),
            "chapters": chapters,
            "url": f"https://www.bilibili.com/video/{bvid}",
        }
    except Exception as e:
        sys.stderr.write(f"[WARN] 获取B站视频详情失败: {e}\n")
        return None


def download_bilibili_chapter(bvid: str, chapter: Dict[str, Any], temp_dir: Path, config: Config) -> Tuple[bool, Optional[Path], int]:
    """使用 yt-dlp 快速提取 B站 指定分 P 的原生高保真 M4A 音频"""
    ep_num = chapter["num"]
    clean_title = sanitize_filename(chapter["title"])
    base_name = f"{ep_num:04d}_{clean_title}"
    target_file = temp_dir / f"{base_name}.m4a"

    if target_file.is_file() and target_file.stat().st_size > 102400:
        return True, target_file, target_file.stat().st_size

    yt_dlp = config.yt_dlp_path or shutil.which("yt-dlp") or "/opt/miniconda3/bin/yt-dlp"
    if not os.path.exists(yt_dlp):
        sys.stderr.write(f"[ERROR] 未找到 yt-dlp 工具，请检查路径: {yt_dlp}\n")
        return False, None, 0

    cmd = [
        yt_dlp,
        "--no-warnings",
        "-x",
        "--audio-format", "m4a",
        "--playlist-items", str(ep_num),
        "-o", f"{temp_dir}/{base_name}.%(ext)s",
        f"https://www.bilibili.com/video/{bvid}"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        # 寻找生成的文件
        for cand in [target_file, temp_dir / f"{base_name}.mp3", temp_dir / f"{base_name}.opus"]:
            if cand.is_file() and cand.stat().st_size > 10240:
                if cand.suffix != ".m4a":
                    cand.replace(target_file)
                return True, target_file, target_file.stat().st_size

        # 若 yt-dlp 加了额外后缀，寻找匹配文件
        matched = list(temp_dir.glob(f"{base_name}*.*"))
        for m in matched:
            if not m.name.endswith(".part") and m.stat().st_size > 10240:
                m.replace(target_file)
                return True, target_file, target_file.stat().st_size

        return False, None, 0
    except Exception as e:
        sys.stderr.write(f"[WARN] 下载B站第 {ep_num} 集失败: {e}\n")
        return False, None, 0


# =====================================================================
# 3. 恋听网 (Ting55) 引擎 (含 DNS 直连 IP 穿透与防盗链解析)
# =====================================================================

_CACHED_REAL_IP: Optional[str] = None

def get_real_ip(config: Config) -> str:
    """自动获取 ting55.com 直连 IP，避免 macOS Fake-IP/TUN 劫持导致握手失败"""
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


def fetch_ting55_html(url: str, config: Config, max_retries: Optional[int] = None, delay: Optional[float] = None) -> str:
    """使用国内 IP 穿透抓取恋听网页面"""
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
            time.sleep(base_delay * attempt + 1.0)
        except Exception:
            time.sleep(2.0)
    return ""


def search_ting55(query: str, config: Config, limit: int = 5) -> List[Dict[str, Any]]:
    """检索恋听网资源"""
    encoded_query = urllib.parse.quote(query.strip())
    search_url = f"https://ting55.com/search/{encoded_query}"
    html = fetch_ting55_html(search_url, config, delay=0.8)
    if not html:
        return []

    books = []
    pattern = (
        r'<li>\s*<div class=\"img\"><a href=\"/book/(\d+)\"[^>]*title=\"([^\"]+)\"[^>]*>'
        r'<img[^>]*src=\"([^\"]+)\"[^>]*alt=\"([^\"]+)\"[^>]*></a></div>\s*'
        r'<div class=\"info\"><h4[^>]*><a[^>]*>(.*?)</a></h4>\s*'
        r'<p>作者：(.*?)</p>\s*<p>播音：(.*?)</p>\s*<p>状态：(.*?)</p>\s*</div>\s*</li>'
    )

    for m in re.finditer(pattern, html, re.DOTALL):
        b_id, title_attr, cover, alt, raw_title, author, broad, status = m.groups()
        clean_title = sanitize_filename(re.sub(r'<[^>]+>', '', raw_title).strip())
        cover_url = cover if cover.startswith("http") else f"https:{cover}"
        books.append({
            "id": f"ting_{b_id}",
            "source_id": b_id,
            "source": "ting55",
            "source_name": "恋听网",
            "title": clean_title,
            "author": author.strip(),
            "broadcaster": broad.strip() or "恋听播音",
            "status": status.strip(),
            "chapters_count": 0,
            "cover_url": cover_url,
            "url": f"https://ting55.com/book/{b_id}",
        })
        if len(books) >= limit:
            break

    return books


def get_ting55_info(book_id: str, config: Config) -> Optional[Dict[str, Any]]:
    """获取恋听网小说目录详情"""
    url = f"https://ting55.com/book/{book_id}"
    html = fetch_ting55_html(url, config, max_retries=4, delay=1.0)
    if not html:
        return None

    title_m = re.search(r'<h1>([^<]+)</h1>', html)
    raw_title = title_m.group(1).strip() if title_m else f"book_{book_id}"
    clean_title = sanitize_filename(raw_title)

    desc_m = re.search(r'<div class=[\"\x27]desc[\"\x27][^>]*>(.*?)</div>', html, re.DOTALL)
    description = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""

    raw_chapters = re.findall(rf'href=[\"\x27]/book/{book_id}-(\d+)[\"\x27][^>]*>([^<]+)</a>', html)
    chapters = []
    seen = set()
    for ch_num_str, ch_name in raw_chapters:
        n = int(ch_num_str)
        if n not in seen:
            seen.add(n)
            chapters.append({
                "num": n,
                "title": sanitize_filename(ch_name.strip()),
                "url": f"https://ting55.com/book/{book_id}-{n}"
            })
    chapters.sort(key=lambda x: x["num"])

    return {
        "id": f"ting_{book_id}",
        "source_id": book_id,
        "source": "ting55",
        "source_name": "恋听网",
        "title": clean_title,
        "raw_title": raw_title,
        "broadcaster": "恋听网播音",
        "description": description[:300],
        "chapters_count": len(chapters),
        "chapters": chapters,
        "url": url,
    }


def download_ting55_chapter(book_id: str, chapter: Dict[str, Any], temp_dir: Path, config: Config) -> Tuple[bool, Optional[Path], int]:
    """下载恋听网单个章节"""
    page_num = chapter["num"]
    clean_title = sanitize_filename(chapter["title"])
    base_name = f"{page_num:04d}_{clean_title}"

    for ext_cand in ["m4a", "mp3"]:
        target_file = temp_dir / f"{base_name}.{ext_cand}"
        if target_file.is_file() and target_file.stat().st_size > 102400:
            return True, target_file, target_file.stat().st_size

    # 解析直链
    real_ip = get_real_ip(config)
    page_url = f"https://ting55.com/book/{book_id}-{page_num}"
    html = fetch_ting55_html(page_url, config, max_retries=3, delay=1.0)
    if not html:
        return False, None, 0

    def _meta(name: str):
        m = re.search(r'<meta\s+name=[\"\x27]' + name + r'[\"\x27]\s+content=[\"\x27]([^\x27\"]+)[\"\x27]', html)
        return m.group(1) if m else ''

    b = _meta('_b') or str(book_id)
    cp = _meta('_cp') or str(page_num)
    p = _meta('_p') or '0'
    c = _meta('_c')
    l = _meta('_l') or '1'
    ext = _meta('_f') or 'm4a'

    if not c:
        return False, None, 0

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
    ] )

    audio_url = None
    for attempt in range(1, config.max_retries + 1):
        try:
            time.sleep(config.rate_limit_delay)
            res = subprocess.check_output(cmd_post, stderr=subprocess.DEVNULL).decode('utf-8', errors='ignore')
            data = json.loads(res)
            audio_url = data.get('ourl') or data.get('url') or data.get('plink')
            if audio_url:
                if '.m4a' in audio_url.lower():
                    ext = 'm4a'
                elif '.mp3' in audio_url.lower():
                    ext = 'mp3'
                break
            elif data.get('status') == -1:
                sys.stderr.write(f"[INFO] 恋听网第 {page_num} 集为付费章节，停止重试\n")
                return False, None, 0
        except Exception:
            time.sleep(1.5)

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
        '-C', '-',
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
# 统一聚合搜索与详情调度
# =====================================================================

def search_audiobooks(query: str, config: Config, limit: int = 5, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """并发跨平台聚合搜索小说有声资源"""
    src = (source or config.default_search_source).lower()
    all_results = []

    tasks = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        if src in ["all", "xm", "ximalaya"] and config.enable_ximalaya:
            tasks[executor.submit(search_ximalaya, query, limit)] = "ximalaya"
        if src in ["all", "bili", "bilibili"] and config.enable_bilibili:
            tasks[executor.submit(search_bilibili, query, limit)] = "bilibili"
        if src in ["all", "ting55", "ting"] and config.enable_ting55:
            tasks[executor.submit(search_ting55, query, config, limit)] = "ting55"

        for future in as_completed(tasks):
            try:
                res = future.result()
                if res:
                    all_results.extend(res)
            except Exception as e:
                sys.stderr.write(f"[WARN] 搜索引擎任务失败: {e}\n")

    # 保存缓存便于直接输入序号下载
    if all_results:
        save_search_cache(all_results)

    return all_results


def get_book_info(target: str, config: Config) -> Optional[Dict[str, Any]]:
    """统一获取小说或专辑详细信息与章节目录"""
    source, source_id, cached = resolve_target(target)

    info = None
    if source == "ximalaya":
        info = get_ximalaya_info(source_id, config)
    elif source == "bilibili":
        info = get_bilibili_info(source_id, config)
    elif source == "ting55":
        info = get_ting55_info(source_id, config)

    # 补充缓存中的额外属性
    if info and cached:
        if not info.get("broadcaster") and cached.get("broadcaster"):
            info["broadcaster"] = cached["broadcaster"]

    return info


def download_single_chapter(source: str, source_id: str, chapter: Dict[str, Any], temp_dir: Path, config: Config) -> Tuple[bool, Optional[Path], int]:
    """统一分发单集音频下载"""
    if source == "ximalaya":
        return download_ximalaya_chapter(source_id, chapter, temp_dir, config)
    elif source == "bilibili":
        return download_bilibili_chapter(source_id, chapter, temp_dir, config)
    elif source == "ting55":
        return download_ting55_chapter(source_id, chapter, temp_dir, config)
    else:
        return False, None, 0


# =====================================================================
# 元数据注入与交付整理 (直接可听 M4A/MP3 + playlist.m3u)
# =====================================================================

def tag_audio_file(filepath: Path, chapter_title: str, broadcaster: str, book_title: str, track_num: int, total_tracks: int):
    """为音频文件注入规范有声书 ID3/MP4 标签"""
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
    """生成 .m3u 连续播放列表文件，便于在 Mac / PC 播放器中全书双击连播"""
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


def package_and_deliver(
    src_folder: Path,
    book_title: str,
    broadcaster: str,
    dest_dir: Path,
    package_mode: str,
    config: Config,
    clean_cache: bool = True
) -> Dict[str, Any]:
    """将下载好的音频文件整理为直接可听的规范专辑目录，生成连播列表并按需归档"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean_name = sanitize_filename(f"《{book_title}》" if not broadcaster or broadcaster in book_title else f"《{book_title}（{broadcaster}）》")

    folder_dest_path = dest_dir / clean_name
    zip_dest_path = dest_dir / f"{clean_name}.zip"

    created_paths = []
    total_size_bytes = 0

    audio_files = sorted([f for f in src_folder.iterdir() if f.is_file() and not f.name.endswith(".part") and not f.name.startswith(".") and f.name != "playlist.m3u"])
    total_count = len(audio_files)

    # 1. 注入有声书标签
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

        if config.generate_m3u_playlist:
            generate_playlist(folder_dest_path, book_title, delivered_audio_files)

        created_paths.append(str(folder_dest_path))

    # 3. ZIP 归档模式 (仅当明确要求时产生)
    if package_mode in ["zip", "both"]:
        with zipfile.ZipFile(zip_dest_path, "w") as zf:
            for audio_f in audio_files:
                arcname = f"{clean_name}/{audio_f.name}"
                zinfo = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
                zinfo.flag_bits |= 0x800
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
# CLI 执行调度
# =====================================================================

def run_search(query: str, limit: int = 5, source: Optional[str] = None, as_json: bool = False, config_path: Optional[str] = None):
    config = Config(config_path)
    candidates = search_audiobooks(query, config, limit=limit, source=source)
    if as_json:
        print(json.dumps(candidates, ensure_ascii=False, indent=2))
        return

    if not candidates:
        print(f"[-] 未找到包含关键词 “{query}” 的有声书，请更换书名或关键词重试。")
        return

    print(f"\n📚 为您找到以下《{query}》相关有声资源（输入编号即可直接下载）：\n")
    source_icons = {
        "ximalaya": "🎧 [喜马拉雅]",
        "bilibili": "📺 [B站/广播剧]",
        "ting55": "📻 [恋听网]"
    }
    for idx, b in enumerate(candidates, 1):
        src_tag = source_icons.get(b.get("source", ""), f"[{b.get('source_name', '平台')}]")
        print(f"{idx}. {b['title']}  {src_tag}")
        print(f"   • 播音/演播: {b['broadcaster']} | 状态: {b['status']}")
        print(f"   • 标识: {b['id']} | 链接: {b['url']}")
        print()
    print("👉 回复或执行: python audiobook_dl.py download 1 开始下载全本或指定章节\n")


def run_info(target: str, as_json: bool = False, config_path: Optional[str] = None):
    config = Config(config_path)
    info = get_book_info(target, config)
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if not info:
        print(f"[-] 获取有声书详情失败: {target}")
        return

    source_icons = {
        "ximalaya": "🎧 喜马拉雅",
        "bilibili": "📺 哔哩哔哩 (B站)",
        "ting55": "📻 恋听网"
    }
    src_display = source_icons.get(info.get("source", ""), info.get("source_name", "未知平台"))

    print(f"\n📖 书名: {info['title']}")
    print(f"• 来源: {src_display}")
    print(f"• 演播/主播: {info.get('broadcaster', '未知')}")
    print(f"• 标识: {info['id']}")
    print(f"• 总集数: {info['chapters_count']} 集")
    if info.get('description'):
        print(f"• 简介: {info['description'][:150]}...")
    if info.get('chapters'):
        print(f"• 目录章节预览 (前 {min(len(info['chapters']), 5)} 集):")
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
    source, source_id, cached = resolve_target(target)

    # 1. 获取书籍/专辑全量信息
    info = get_book_info(target, config)
    if not info:
        err = {"status": "error", "error": f"无法获取有声资源 ({target}) 的详情"}
        if as_json:
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"[-] {err['error']}")
        return

    book_title = info["title"]
    broadcaster = info.get("broadcaster", "")
    total_chapters = info.get("chapters_count", 0)

    # 2. 确定章节区间
    s_ep = max(1, start_ep or 1)
    e_ep = end_ep or (s_ep + limit - 1 if limit else total_chapters)
    if config.default_max_chapters > 0 and end_ep is None and limit is None:
        e_ep = min(e_ep, s_ep + config.default_max_chapters - 1)

    if total_chapters > 0 and e_ep > total_chapters:
        e_ep = total_chapters

    # 3. 准备章节清单
    if source == "ximalaya":
        # 喜马拉雅按需分页拉取目标章节
        selected_chapters = get_ximalaya_tracks_range(source_id, s_ep, e_ep)
    else:
        # B站或恋听网从已知 chapters 中过滤
        all_chapters = info.get("chapters", [])
        selected_chapters = [c for c in all_chapters if s_ep <= c["num"] <= e_ep]

    if not selected_chapters:
        err = {"status": "error", "error": f"选定章节区间为空 (第 {s_ep} 至 {e_ep} 集，全书共 {total_chapters} 集)"}
        if as_json:
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"[-] {err['error']}")
        return

    # 4. 准备目录
    final_dest_dir = Path(os.path.expanduser(dest_dir or config.audiobook_dir))
    temp_work_dir = final_dest_dir / ".cache_downloads" / f"{source}_{source_id}_{sanitize_filename(book_title)}"
    temp_work_dir.mkdir(parents=True, exist_ok=True)

    actual_package_mode = (package_mode or config.package_format).lower()

    if not as_json:
        print(f"\n==================================================")
        print(f"🚀 开始下载有声书: 《{book_title}》")
        print(f"• 来源平台: {info.get('source_name', source)} (标识: {info['id']})")
        if broadcaster:
            print(f"• 演播/主播: {broadcaster}")
        print(f"• 计划章节: 第 {s_ep} 集 至 第 {e_ep} 集 (共计 {len(selected_chapters)} 集)")
        print(f"• 目标目录: {final_dest_dir}")
        print(f"• 交付格式: {actual_package_mode} (整理直接可听规范专辑文件夹)")
        print(f"==================================================\n")

    # 5. 下载章节
    downloaded_files = []
    failed_chapters = []

    for idx, ch in enumerate(selected_chapters, 1):
        if not as_json:
            print(f"[{idx}/{len(selected_chapters)}] 正在下载 第 {ch['num']} 集: {ch['title']} ...")
        ok, filepath, size_bytes = download_single_chapter(source, source_id, ch, temp_work_dir, config)
        if ok and filepath:
            downloaded_files.append(filepath)
            if not as_json:
                size_mb = size_bytes / (1024 * 1024)
                print(f"    -> [成功] {filepath.name} ({size_mb:.1f} MB)")
        else:
            failed_chapters.append(ch['num'])
            if not as_json:
                print(f"    -> [失败] 第 {ch['num']} 集下载未完成")

    if not downloaded_files:
        err = {"status": "error", "error": "所选章节均未能成功下载音频源"}
        if as_json:
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"\n[-] {err['error']}")
        return

    # 6. 整理为直接可听专辑目录并生成 playlist.m3u
    if not as_json:
        print(f"\n🎵 正在将已下载的 {len(downloaded_files)} 个音频章节整理为可原生收听的专辑文件夹...")

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
        "source": source,
        "source_name": info.get("source_name", source),
        "book_id": info["id"],
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
        print(f"✅ 有声书下载完成，已整理为直接可播放格式！")
        print(f"• 书名: 《{book_title}》")
        if broadcaster:
            print(f"• 演播: {broadcaster}")
        print(f"• 章节: 第 {s_ep} 集 至 第 {e_ep} 集 (成功: {len(downloaded_files)} 集, 失败: {len(failed_chapters)} 集)")
        print(f"• 总计体积: {package_res['total_size_mb']} MB")
        print(f"• 交付格式: 直接可听音频 (M4A 规范专辑目录)")
        print(f"• 音频目录: {package_res['primary_path']}")
        if package_res.get("playlist_path"):
            print(f"• 连播列表: {package_res['playlist_path']} (双击即可连续播放)")
        print(f"=" * 50 + "\n")


# =====================================================================
# Main 主入口
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Hermes 多源听书检索、下载与直接可听整理工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. 搜索
    search_parser = subparsers.add_parser("search", help="搜索有声书")
    search_parser.add_argument("query", help="小说书名、作者或广播剧关键词 (如 '官道无疆')")
    search_parser.add_argument("--source", choices=["all", "xm", "bili", "ting55"], default="all", help="指定源 (默认 all 聚合所有源)")
    search_parser.add_argument("--limit", type=int, default=5, help="最多返回候选数 (默认 5)")
    search_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    search_parser.add_argument("--config", help="指定配置文件路径")

    # 2. 详情
    info_parser = subparsers.add_parser("info", help="查看有声书详情与章节选集")
    info_parser.add_argument("target", help="标识、链接或上一次搜索的序号 (如 '1' 或 'xm_49866738')")
    info_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    info_parser.add_argument("--config", help="指定配置文件路径")

    # 3. 下载
    dl_parser = subparsers.add_parser("download", help="下载并整理有声书为直接可听目录")
    dl_parser.add_argument("target", help="标识、链接或上一次搜索的序号 (如 '1' 或 'xm_49866738')")
    dl_parser.add_argument("--start", type=int, default=1, help="起始集数 (默认 1)")
    dl_parser.add_argument("--end", type=int, default=None, help="结束集数 (默认最后一集)")
    dl_parser.add_argument("--limit", type=int, default=None, help="最多下载多少集")
    dl_parser.add_argument("--package", choices=["folder", "both", "zip"], help="交付模式: folder (直接可听目录), both 或 zip")
    dl_parser.add_argument("--dest", help="自定义保存的目标目录 (覆盖配置文件)")
    dl_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    dl_parser.add_argument("--config", help="指定配置文件路径")

    args = parser.parse_args()

    if args.command == "search":
        run_search(args.query, limit=args.limit, source=args.source, as_json=args.json, config_path=args.config)
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
