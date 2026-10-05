# AI 有声书自动化下载与即听交付服务 (ai-audiobook-download)

本模块参考 [`/Users/log/Documents/ai-music-download`](file:///Users/log/Documents/ai-music-download) 的 Skill + CLI 模式进行设计与构建，专为通过 **Telegram 发出指令**、自动化解析抓取国内有声书平台音频、并将整本小说或选定章节**直接保存为可原生收听的音频格式（M4A/MP3 规范专辑目录 + playlist.m3u 连续播放列表）归档到指定目录**而开发。

---

## 核心特性与架构

1. **直接可听交付（免解压）**:
   - 默认采用 `folder` 模式，所有章节直接下载为规范命名的标准音频文件（如 `0001_第1章.m4a`）。
   - 自动内嵌 ID3/MP4 有声书元数据标签（书名、演播人、章节序号、音轨号），在 macOS 音乐、iPhone 图书/文件、VLC 中自动识别专辑与音轨。
   - 自动生成 `playlist.m3u` 连续播放列表，双击即可无缝全书连续收听。
   - （可选）若特定场景需要打包，也支持 `--package zip`。
2. **双模运行 (Dual-Mode)**:
   - **Hermes Agent Skill 模式**（推荐，与 `ai-music-download` 一致）：直接在 Telegram 与您的 Hermes 智能体对话（例如发送 `“搜听书 官道无疆”`、`“下载 1 前20集”`），由 Hermes 理解自然语言并调用底层脚本。
   - **独立 Telegram Bot 模式**（可选守护进程）：通过内置的 `scripts/tg_bot.py`，支持直接通过 `/search` 和 `/download` 命令极速下载。对于下载量适中（<= 5集）的任务，Bot 还会直接使用 Telegram 原生音频播放器将音频文件推送到聊天窗口中，点开即可在 Telegram 内边聊边听。
3. **网络与反爬直连穿透**:
   - 自动解析国内公共 DNS（阿里 223.5.5.5 等），绑定直连 IP（`47.238.53.179`），**彻底解决科学上网 / TUN 虚拟网卡 (Fake-IP `198.18.x.x`) 导致的 TLS 握手断开 (SSL 35 错误)**。
   - 具备自适应梯度退避重试，避开恋听网对高频握手的频控封锁。
   - 直接从底层喜马拉雅等高速 CDN 获取无损/高保真音频流（AAC/M4A 格式，单集约 20 分钟/10MB）。
4. **断点续传与缓存**:
   - 自动检测并跳过已经下载完整的章节，下载中断后随时可以继续。
   - 搜索结果自动缓存，用户在 Telegram 里仅需回复数字 `1`、`2` 即可发起下载。

---

## 目录结构

```text
/Users/log/Documents/ai-audiobook-download/
├── SKILL.md                          # Hermes 技能定义文件（触发词、交互规范、Prompt规范）
├── config.example.yaml               # 配置模板
├── config.yaml                       # 运行时配置文件（目标存储目录、交付格式等）
├── README.md                         # 详细技术说明文档
└── scripts/
    ├── audiobook_dl.py               # 核心 CLI 工具（搜索、详情、批量下载、断点续传、元数据注入）
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

# 是否自动为音频注入元数据标签 (书名、演播者、音轨号)
embed_metadata: true

# 是否自动生成 .m3u 连续播放列表
generate_m3u_playlist: true

# 请求间隔延迟（秒，推荐 1.5 - 2.5 秒，避免触发 IP 握手拦截）
rate_limit_delay: 2.0

# 失败重试次数
max_retries: 5

# 国内直连 IP 穿透配置
direct_ip: "47.238.53.179"
auto_resolve_dns: true
```

---

## CLI 命令使用说明

### 1. 搜索小说
```bash
python scripts/audiobook_dl.py search "官道无疆"
```

### 2. 查看小说详情与目录
```bash
# 传入序号或纯数字 ID
python scripts/audiobook_dl.py info 1
python scripts/audiobook_dl.py info 11229
```

### 3. 下载并保存为直接可听目录
```bash
# 下载上一次搜索的第 1 本书的前 20 集，保存为直接可听的专辑目录到 audiobook_dir
python scripts/audiobook_dl.py download 1 --start 1 --end 20

# 下载第 21 到 50 集
python scripts/audiobook_dl.py download 1 --start 21 --end 50

# 指定输出到 NAS 或移动硬盘
python scripts/audiobook_dl.py download 11229 --start 1 --end 50 --dest "/Volumes/NAS/Audiobooks"
```

---

## Telegram 交互流程示例

### 1. Hermes Agent 模式 (在 Telegram 私聊中发送)
1. **用户**：`搜听书 官道无疆`
2. **Hermes**：
   ```text
   📚 为您找到以下《官道无疆》相关版本：

   1. 《官道无疆》- 播音：小玩纸先生 (共1616集 | 完结) ⭐推荐
   2. 《官道无疆》- 播音：袏佑 (共820集 | 完结)
   3. 《官道奇才》- 播音：幻多奇 (共784集 | 完结)

   👉 请回复数字选择版本，例如：
   • “1” 或 “下载 1” （下载全本直接收听）
   • “1 前20集” （下载前20集）
   ```
3. **用户**：`1 前20集`
4. **Hermes** 执行下载并回复：
   ```text
   ✅ 有声书下载完成，已保存为直接可听格式！

   • 书名：《官道无疆（小玩纸先生）》
   • 演播：小玩纸先生
   • 章节：第 1 集 至 第 20 集 (共 20 集)
   • 规格：高保真 AAC (M4A，已注入书名与音轨标签)
   • 总体积：190.5 MB
   • 交付格式：直接可听音频 (M4A 专辑目录)
   • 本地目录：/Users/log/Downloads/Audiobooks/《官道无疆（小玩纸先生）》
   • 连播列表：/Users/log/Downloads/Audiobooks/《官道无疆（小玩纸先生）》/playlist.m3u

   无需解压，点击文件夹内任意章节或双击 playlist.m3u 即可立即连续收听。
   ```

### 2. 独立 Telegram Bot 模式
在终端运行守护进程：
```bash
python scripts/tg_bot.py
```
在 Telegram 中向您的机器人直接发送：
- `/search 官道无疆`
- `/download 1 1-5`
- 机器人不仅完成本地归档，还会将音频文件使用 Telegram 原生音频播放器直接发送到聊天窗口中，点击即可后台收听。
