---
name: audiobook-download
description: "Search, download, and organize audiobooks from Ximalaya (喜马拉雅), Bilibili (B站), and Ting55 (恋听网) into directly playable audio albums (M4A/MP3 with ID3 tags and playlist.m3u) or optional archives via Telegram."
version: 1.2.0
author: Hermes Agent
license: MIT
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [Audiobook, Audio, Telegram, Download, Lossless, M4A, Playable, Ximalaya, Bilibili, Ting55]
    related_skills: []
---

# Telegram 多源听书检索、下载与即听交付工具 (audiobook-download)

## When to use

当用户在 Telegram、对话框或自动化任务中表达想要检索、下载有声书、小说广播剧音频，或要求将听书全本/指定章节保存为直接可听的格式到指定目录时触发本技能。常见触发语句包括：
- “下载听书 官道无疆”
- “搜听书 凡人修仙传”
- “帮我找一下大奉打更人的有声小说或广播剧”
- “下载 官道无疆 喜马拉雅版 前20集”
- “帮我下载B站上的这个广播剧 https://www.bilibili.com/video/BV125SmYkEew”
- “把官道无疆下载到听书目录”

支持同时从 **喜马拉雅 (Ximalaya FM)**、**哔哩哔哩 (Bilibili/B站广播剧)** 以及 **恋听网 (Ting55)** 三大平台聚合检索与解析高保真 AAC/M4A 音频流，批量下载并自动注入有声书标签（书名、主播/UP主、章节名、音轨号）、自动生成 `playlist.m3u` 连续播放列表，**保存为直接可点播收听的原生音频格式**（默认无需解压）。

---

## 支持资源平台特性

1. **🎧 喜马拉雅 (Ximalaya FM)**：
   - 官方出版社与专业演播工作室出品，录音音质极高。
   - 直接解析高保真 64kbps/128kbps AAC M4A 直链，秒级下载。
   - 标识前缀：`xm_<album_id>`（如 `xm_49866738`）。
2. **📺 哔哩哔哩 (Bilibili B站)**：
   - 海量长篇广播剧、分P多人精装剧、UP主完整搬运。
   - 内建 `yt-dlp` 高速分P音频提取（免重编码直接解流为原生 M4A）。
   - 标识前缀：`bili_<bvid>` 或标准 `BV...`。
3. **📻 恋听网 (Ting55)**：
   - 传统经典网络小说聚合。
   - 内建国内 DNS 直连 IP 穿透，自动规避 macOS TUN / Fake-IP 劫持握手问题。
   - 标识前缀：`ting_<book_id>`。

---

## Setup 环境准备

### 1. 依赖工具
- **curl**（系统内置，支持 HTTP/1.1、SNI 直连 IP 穿透与断点续传）
- **yt-dlp**（用于 B 站音视频流高速解密与分P音频提取，通常位于 `/opt/miniconda3/bin/yt-dlp`）
- **Python 3.8+**

### 2. Python 依赖安装
```bash
pip install pyyaml requests mutagen
```

### 3. 配置 `config.yaml`
```yaml
audiobook_dir: "~/Downloads/Audiobooks"
package_format: "folder"  # folder (直接可听目录), both, zip

sources:
  ximalaya: true   # 喜马拉雅 FM
  bilibili: true   # 哔哩哔哩 B站
  ting55: true     # 恋听网

default_search_source: "all"  # all, ximalaya, bilibili, ting55
yt_dlp_path: "/opt/miniconda3/bin/yt-dlp"

embed_metadata: true
generate_m3u_playlist: true
```

---

## Helper Script 使用说明

脚本路径位于 `SKILL_DIR/scripts/audiobook_dl.py`。

### 1. 跨平台聚合检索 (`search`)
```bash
# 同时聚合搜索喜马拉雅、B站、恋听网
python SKILL_DIR/scripts/audiobook_dl.py search "官道无疆" --limit 5

# 指定只搜索某一个平台
python SKILL_DIR/scripts/audiobook_dl.py search "官道无疆" --source xm
python SKILL_DIR/scripts/audiobook_dl.py search "官道无疆" --source bili
python SKILL_DIR/scripts/audiobook_dl.py search "官道无疆" --source ting55
```

### 2. 查看章节选集详情 (`info`)
支持传入候选序号（`1`, `2`...）、平台带前缀 ID 或直接传入网页链接：
```bash
python SKILL_DIR/scripts/audiobook_dl.py info 1
python SKILL_DIR/scripts/audiobook_dl.py info xm_49866738
python SKILL_DIR/scripts/audiobook_dl.py info BV125SmYkEew
```

### 3. 下载并整理为直接可听目录 (`download`)
默认使用 `folder` 模式，直接保存为可原生点击播放的规范专辑文件夹，内含音频与 `playlist.m3u` 连播列表：
```bash
# 方式 1: 直接按检索序号下载第 1 个版本（例如喜马拉雅版）的前 20 集
python SKILL_DIR/scripts/audiobook_dl.py download 1 --start 1 --end 20

# 方式 2: 下载 B 站广播剧指定分 P
python SKILL_DIR/scripts/audiobook_dl.py download BV125SmYkEew --start 1 --end 5

# 方式 3: 输出到自定义 NAS 目录
python SKILL_DIR/scripts/audiobook_dl.py download 1 --start 1 --end 50 --dest "/Volumes/NAS/Audiobooks"

# 方式 4: 若明确要求生成 zip 归档
python SKILL_DIR/scripts/audiobook_dl.py download 1 --start 1 --end 20 --package zip
```

---

## Telegram 交互流程与回复规范

作为 Hermes Agent，在通过 Telegram 处理用户的听书指令时，遵循以下交互原则：

### 第一步：响应检索
当收到用户“下载听书 <书名>”或“搜听书 <书名>”时，调用 `search` 命令。向 Telegram 用户展示候选版本（带清晰平台标识）：
```text
📚 为您找到以下《官道无疆》相关有声资源，请直接回复数字选择：

1. 《官道无疆》 🎧 [喜马拉雅]
   • 播音：杨贺升 (免费完结 | 共1710集) ⭐推荐官方音质
2. 《官道无疆-Z精品全集》 📺 [B站/广播剧]
   • 演播：ZH有声 (93集长音频 | 播放量10万+)
3. 《官道无疆》 📻 [恋听网]
   • 播音：小玩纸先生 (共1616集 | 完结)

👉 请回复数字选择版本，例如：
• “1” 或 “下载 1” （下载全本直接收听）
• “1 前20集” 或 “下载 1 1-50” （下载指定章节）
```

### 第二步：执行下载与即听交付回报
收到用户选择的数字与章节要求后，执行 `download` 命令。任务完成后向 Telegram 用户反馈交付结果：
```text
✅ 有声书下载完成，已整理为直接可听格式！

• 书名：《官道无疆》
• 平台：喜马拉雅 (xm_49866738)
• 演播：杨贺升
• 章节：第 1 集 至 第 20 集（共计 20 集）
• 规格：高保真 AAC (M4A，已注入书名与音轨标签)
• 总体积：72.5 MB
• 本地目录：/Users/log/Downloads/Audiobooks/《官道无疆（杨贺升）》
• 连播列表：/Users/log/Downloads/Audiobooks/《官道无疆（杨贺升）》/playlist.m3u

无需解压，点击文件夹内任意章节或双击 playlist.m3u 即可立即连续收听。
```

---

## 异常处理 (Error Handling)

1. **格式偏好**：
   - 默认直接输出原生标准音频文件夹（无需解压）。仅在用户明确指示“打包成zip”或“给我压缩包”时，才使用 `--package zip`。
2. **长篇小说集数建议**：
   - 喜马拉雅或恋听网长篇小说常达 1000~2000 集，若用户未指定集数，建议在回报中告知全书集数，并优先推荐先下载前 30 集或 50 集。
3. **VIP 付费章节检测**：
   - 对于喜马拉雅或恋听网中的付费章节，工具会自动跳过并提示跳过原因，不会陷入无限卡顿重试。
4. **播放器兼容性**：
   - 所有生成的 `.m4a` 文件均内嵌了 ID3/MP4 有声书元数据标签（Title、Artist、Album、Track Number、Genre=Audiobook），全面适配 macOS 音乐/Books App、iOS 隔空投送、VLC、车载播放器等全生态设备。
