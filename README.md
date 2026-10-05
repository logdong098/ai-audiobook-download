# AI 多源有声书自动化下载与即听交付服务 (ai-audiobook-download)

本模块参考 [`/Users/log/Documents/ai-music-download`](file:///Users/log/Documents/ai-music-download) 的 Skill + CLI 模式进行设计与构建，专为通过 **Telegram 发出指令**、自动化解析抓取国内有声书平台音频、并将整本小说或选定章节**直接保存为可原生收听的音频格式（M4A/MP3 规范专辑目录 + playlist.m3u 连续播放列表）归档到指定目录**而开发。

已聚合三大主流中文有声资源平台：
1. **喜马拉雅 FM (Ximalaya)**：官方出版与演播室高保真有声小说（直解高音质 M4A CDN 直链，秒级下载）
2. **哔哩哔哩 (Bilibili B站)**：海量长篇广播剧、分P多人有声剧（基于 `yt-dlp` 原生音轨解流，无损提取为 M4A）
3. **恋听网 (Ting55)**：传统网络小说经典播音有声源（内建国内 DNS 直连 IP 穿透，彻底解决 Fake-IP/TUN 握手问题）

---

## 核心特性与架构

1. **多源并发聚合检索**:
   - 一次指令同时在喜马拉雅、B站广播剧、恋听网三方进行并发检索。
   - 标注清晰的平台标签（`🎧 [喜马拉雅]`、`📺 [B站/广播剧]`、`📻 [恋听网]`），显示播音演播者、全书集数、播放热度与状态。
   - 支持通过 `--source xm|bili|ting55` 指定只从单一平台检索。
2. **直接可听交付（免解压）**:
   - 默认采用 `folder` 模式，所有章节直接下载为规范命名的标准音频文件（如 `0001_第1集.m4a`）。
   - 自动内嵌 ID3/MP4 有声书元数据标签（书名、演播人、章节序号、音轨号），在 macOS 音乐、iPhone 图书/文件、VLC 中自动识别专辑与音轨。
   - 自动生成 `playlist.m3u` 连续播放列表，双击即可无缝全书连续收听。
   - （可选）若特定场景需要归档打包，也支持 `--package zip` 或 `both`。
3. **双模运行 (Dual-Mode)**:
   - **Hermes Agent Skill 模式**（推荐，与 `ai-music-download` 一致）：直接在 Telegram 与您的 Hermes 智能体对话（例如发送 `“搜听书 凡人修仙传”`、`“下载 1 前20集”`），由 Hermes 理解自然语言并调用底层脚本。
   - **独立 Telegram Bot 模式**（可选守护进程）：通过内置的 `scripts/tg_bot.py`，支持直接通过 `/search` 和 `/download` 命令极速下载。对于下载量适中（<= 5集）的任务，Bot 还会直接使用 Telegram 原生音频播放器将音频文件推送到聊天窗口中，点开即可在 Telegram 内边聊边听。
4. **断点续传与缓存**:
   - 自动检测并跳过已经下载完整的章节，下载中断后随时可以继续。
   - 搜索结果自动缓存，用户在 Telegram 里仅需回复数字 `1`、`2` 即可发起下载。

---

## 目录结构

```text
/Users/log/Documents/ai-audiobook-download/
├── SKILL.md                          # Hermes 技能定义文件（触发词、交互规范、Prompt规范）
├── config.example.yaml               # 配置模板
├── config.yaml                       # 运行时配置文件（目标存储目录、多源开关等）
├── README.md                         # 详细技术说明文档
└── scripts/
    ├── audiobook_dl.py               # 核心 CLI 工具（多源搜索、详情、批量下载、断点续传、元数据注入）
    └── tg_bot.py                     # 独立 Telegram 轮询守护进程（可选，支持群聊/私聊直接点播）
```

---

## 快速配置 (`config.yaml`)

编辑 [`config.yaml`](file:///Users/log/Documents/ai-audiobook-download/config.yaml)：

```yaml
# 听书文件保存的目标绝对路径
audiobook_dir: "~/Downloads/Audiobooks"

# 交付工作模式:
# - "folder" : 直接保存为规范的有声书专辑文件夹，包含直接可听的音频与 playlist.m3u (默认推荐)
# - "both"   : 既保留直接可听的规范目录，同时生成一份 .zip 归档
# - "zip"    : 仅打包为单个 .zip 压缩包
package_format: "folder"

# 多源平台配置
sources:
  ximalaya: true   # 喜马拉雅 FM
  bilibili: true   # 哔哩哔哩 B站
  ting55: true     # 恋听网

# 默认搜索聚合模式: "all" (聚合所有源), "ximalaya", "bilibili", "ting55"
default_search_source: "all"

# yt-dlp 可执行文件路径 (用于 B 站音视频流提取)
yt_dlp_path: "/opt/miniconda3/bin/yt-dlp"

# 是否自动为音频注入元数据标签 (书名、演播者、音轨号)
embed_metadata: true

# 是否自动生成 .m3u 连续播放列表
generate_m3u_playlist: true
```

---

## CLI 命令使用说明

### 1. 跨平台搜索
```bash
# 聚合搜索喜马拉雅、B站、恋听网
python scripts/audiobook_dl.py search "官道无疆"

# 指定搜索喜马拉雅
python scripts/audiobook_dl.py search "官道无疆" --source xm

# 指定搜索 B 站广播剧
python scripts/audiobook_dl.py search "凡人修仙传" --source bili
```

### 2. 查看小说详情与分P选集
```bash
# 传入搜索结果序号 (1, 2, 3...)
python scripts/audiobook_dl.py info 1

# 传入喜马拉雅专辑 ID
python scripts/audiobook_dl.py info xm_49866738

# 传入 B 站 BV 号
python scripts/audiobook_dl.py info BV125SmYkEew
```

### 3. 下载并保存为直接可听目录
```bash
# 下载上一次搜索的第 1 本书（如喜马拉雅版）的前 20 集，保存为规范专辑目录
python scripts/audiobook_dl.py download 1 --start 1 --end 20

# 下载 B 站广播剧指定分 P
python scripts/audiobook_dl.py download BV125SmYkEew --start 1 --end 10

# 指定输出到 NAS 或移动硬盘
python scripts/audiobook_dl.py download 1 --start 1 --end 50 --dest "/Volumes/NAS/Audiobooks"

# 若明确要求生成 zip 归档
python scripts/audiobook_dl.py download 1 --start 1 --end 20 --package zip
```

---

## 交付产物结构展示

下载完成后，在目标目录下自动组织为直接可播放的专辑结构：

```text
~/Downloads/Audiobooks/《官道无疆（杨贺升）》/
├── 0001_官道无疆 000楔子.m4a       # 原生高保真音频，已嵌入 ID3 有声书标签
├── 0002_官道无疆 楔子.m4a
├── 0003_官道无疆 001.m4a
├── 0004_官道无疆 002.m4a
└── playlist.m3u                    # 连续播放列表，双击直接调用系统播放器连播全书
```

用户无需任何解压操作，点击文件夹中任意音频或双击 `playlist.m3u` 即可无缝享受高保真有声体验。
