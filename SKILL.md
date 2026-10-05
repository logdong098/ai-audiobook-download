---
name: audiobook-download
description: "Search, download, and organize audiobooks from Ting55 (恋听网) and mainstream Chinese audio platforms into directly playable audio albums (M4A/MP3 with ID3 tags and playlist.m3u) or optional archives via Telegram."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [Audiobook, Audio, Telegram, Download, Lossless, M4A, Playable, Ting55]
    related_skills: []
---

# Telegram 听书检索、下载与即听交付工具 (audiobook-download)

## When to use

当用户在 Telegram、对话框或自动化任务中表达想要检索、下载有声书、小说音频，或要求将听书全本/指定章节保存为直接可听的格式到指定目录时触发本技能。常见触发语句包括：
- “下载听书 官道无疆”
- “搜听书 凡人修仙传”
- “帮我找一下大奉打更人的有声小说”
- “帮我下载这本有声书 https://ting55.com/book/11229”
- “下载 官道无疆 前20集”
- “把官道无疆下载到听书目录”

支持从恋听网 (Ting55) 等主流公开有声书聚合源检索与解析，穿透国内直连 CDN（高保真 AAC/M4A 或 320k MP3），批量下载并自动注入有声书标签（书名、主播、章节名、音轨号）、自动生成 `playlist.m3u` 连续播放列表，**保存为直接可点播收听的原生音频格式**（默认无需解压）。

---

## Setup 环境准备

### 1. 依赖工具
- **curl**（系统内置，支持 HTTP/1.1、SNI 直连 IP 穿透与断点续传）
- **Python 3.8+**

### 2. Python 依赖安装
```bash
pip install pyyaml requests mutagen
```

### 3. 配置 `config.yaml`
在当前 Skill 目录创建 `config.yaml`（可从 `config.example.yaml` 复制）：
```yaml
# 听书文件保存的目标绝对路径
audiobook_dir: "~/Downloads/Audiobooks"

# 交付模式:
# - "folder" : 直接保存为可原生点击收听的小说专辑目录（内含 M4A/MP3 音频与 playlist.m3u 连播列表，默认推荐）
# - "both"   : 既保留直接可听的音频目录，同时生成一份 .zip 压缩包
# - "zip"    : 仅打包为单个 .zip 压缩包
package_format: "folder"

# 是否自动为音频注入有声书元数据标签 (书名、演播人、章节序号、音轨号)
embed_metadata: true

# 是否自动生成 playlist.m3u 连续播放列表 (支持在 macOS 音乐/VLC 等播放器中双击全书连续播放)
generate_m3u_playlist: true

# 抓取频控与反爬防护 (秒，建议 1.5 - 2.5 秒)
rate_limit_delay: 2.0

# 失败重试次数
max_retries: 5

# 国内直连 IP 穿透配置 (避免本地 Clash / TUN 虚拟网卡 Fake-IP 劫持握手)
direct_ip: "47.238.53.179"
auto_resolve_dns: true
dns_servers:
  - "223.5.5.5"
  - "119.29.29.29"
  - "114.114.114.114"
```

---

## Helper Script 使用说明

脚本路径位于 `SKILL_DIR/scripts/audiobook_dl.py`。

### 1. 检索有声书 (`search`)
```bash
python SKILL_DIR/scripts/audiobook_dl.py search "<书名或作者>" --limit 5
```

**输出示例 (JSON)**：
```json
[
  {
    "id": "11229",
    "title": "官道无疆（小玩纸先生）",
    "author": "瑞根",
    "broadcaster": "小玩纸先生",
    "status": "完结",
    "source": "恋听网 (Ting55)",
    "url": "https://ting55.com/book/11229"
  },
  {
    "id": "7097",
    "title": "官道无疆",
    "author": "瑞根",
    "broadcaster": "袏佑",
    "status": "完结",
    "source": "恋听网 (Ting55)",
    "url": "https://ting55.com/book/7097"
  }
]
```

### 2. 查看章节详情 (`info`)
支持传入小说数字 ID、URL 或上一次检索的 **序号数字**（`1`, `2`...）：
```bash
python SKILL_DIR/scripts/audiobook_dl.py info 1
```

### 3. 下载并保存为直接可听格式 (`download`)
默认采用 `folder` 模式，直接保存为标准规范的有声书音频文件夹，免去解压步骤：
```bash
# 方式 1: 直接按检索序号下载第 2 本书的前 20 集，保存为直接可听目录
python SKILL_DIR/scripts/audiobook_dl.py download 2 --start 1 --end 20

# 方式 2: 下载指定章节，输出到自定义媒体目录
python SKILL_DIR/scripts/audiobook_dl.py download 11229 --start 1 --end 50 --dest "/Volumes/NAS/Audiobooks"

# 方式 3: 若用户明确要求压缩包，可加 --package zip
python SKILL_DIR/scripts/audiobook_dl.py download 11229 --start 1 --end 20 --package zip
```

**执行结果 (JSON)**：
```json
{
  "status": "success",
  "book_id": "11229",
  "book_title": "官道无疆（小玩纸先生）有声小说",
  "broadcaster": "小玩纸先生",
  "start_chapter": 1,
  "end_chapter": 20,
  "total_chapters_downloaded": 20,
  "failed_chapters_count": 0,
  "package_format": "folder",
  "primary_path": "/Users/log/Downloads/Audiobooks/《官道无疆（小玩纸先生）有声小说》",
  "playlist_path": "/Users/log/Downloads/Audiobooks/《官道无疆（小玩纸先生）有声小说》/playlist.m3u",
  "total_size_mb": 190.5,
  "dest_dir": "/Users/log/Downloads/Audiobooks"
}
```

---

## Telegram 交互流程与回复规范

作为 Hermes Agent，在通过 Telegram 处理用户的听书指令时，请遵循以下交互原则：

### 第一步：响应检索
当收到用户“下载听书 <书名>”或“搜听书 <书名>”时，调用 `search` 命令。向 Telegram 用户展示候选版本：
```text
📚 为您找到以下《官道无疆》有声书版本，请直接回复数字选择：

1. 《官道无疆》- 播音：小玩纸先生 (共1616集 | 完结) ⭐推荐版本
2. 《官道无疆》- 播音：袏佑 (共820集 | 完结)
3. 《官道奇才》- 播音：幻多奇 (共784集 | 完结)

👉 请回复数字选择版本，例如：
• “1” 或 “下载 1” （下载全本直接收听）
• “1 前20集” 或 “下载 1 1-50” （下载指定章节）
```

### 第二步：执行下载与即听交付回报
收到用户选择的数字与章节要求后，执行 `download` 命令。任务完成后向 Telegram 用户反馈交付结果：
```text
✅ 有声书下载完成，已保存为直接可听格式！

• 书名：《官道无疆（小玩纸先生）》
• 演播：小玩纸先生
• 章节：第 1 集 至 第 20 集（共计 20 集）
• 规格：高保真 AAC (M4A，已注入书名与音轨标签)
• 总体积：190.5 MB
• 本地目录：/Users/log/Downloads/Audiobooks/《官道无疆（小玩纸先生）》
• 连播列表：/Users/log/Downloads/Audiobooks/《官道无疆（小玩纸先生）》/playlist.m3u

无需解压，点击文件夹内任意章节或双击 playlist.m3u 即可立即连续收听。
```

---

## 异常处理 (Error Handling)

1. **格式偏好**：
   - 默认直接输出标准音频文件（无需解压）。仅在用户明确指示“打包成zip”或“给我压缩包”时，才使用 `--package zip`。
2. **长篇小说集数建议**：
   - 针对上千集的小说，若用户未指定集数，建议在回报中告知全书集数，并优先推荐先听前 50 集或 100 集。
3. **播放兼容性**：
   - 生成的 `.m4a` / `.mp3` 文件经 `mutagen` 写入了标准有声书元数据标签（Title、Artist/Performer、Album、Track Number、Genre=Audiobook），支持 macOS 音乐、iOS 自带图书/文件 App、VLC 以及各类车载播放器直接读取连续播放。
