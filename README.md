# 🎬 抖音 / TikTok / YouTube Shorts 视频链接自动嗅探器

基于 Playwright 的抖音 / TikTok / YouTube Shorts 视频链接批量抓取工具。

## 工作原理

1. 打开抖音、TikTok 或 YouTube Shorts 页面
2. 抖音：对每个视频按 **`V` 键** 直接复制当前视频链接
3. TikTok：按 **`V`** 打开分享，再按 **`Enter`**，点击页面中的 **`Copy` / `Copy link`**
4. 通过 `navigator.clipboard.readText()` 读取剪贴板
5. 抖音按 `↓`，TikTok 通过滚轮切换到下一个视频
6. 启动后会先检查登录态（YouTube Shorts 默认不强制登录）
7. 结果实时保存到 `scraped_data.json`

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
python3 -m playwright install chromium

# 2. 运行（首次会弹出浏览器，请手动登录抖音）
python3 main.py
python3 main.py --platform tiktok
python3 main.py --platform youtube
python3 main.py --platform youtube --download-ytdlp

# 2.1 连接手动启动的真实 Chrome（适合 TikTok 登录）
# 先完全退出现有 Chrome，再单独启动：
# /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
# 然后运行：
python3 main.py --platform tiktok --debug-only --attach-real-chrome

# 3. 更多选项
python3 main.py --max 200           # 抓 200 条
python3 main.py --output data.json  # 自定义输出文件
python3 main.py --headless          # 无界面模式
python3 main.py --platform youtube --download-ytdlp --ytdlp-output-dir downloads/shorts
```

`yt-dlp` 下载常用参数：

```bash
python3 main.py --platform youtube --download-ytdlp \
  --ytdlp-format "bv*+ba/b" \
  --ytdlp-merge-format mp4 \
  --ytdlp-cookies-from-browser chrome \
  --ytdlp-archive-file logs/yt_dlp_downloaded.txt
```

持续运行并在异常退出后自动重启：

```bash
chmod +x run_forever.sh
./run_forever.sh
```

可选环境变量：

```bash
PYTHON_BIN=/Users/tailab/miniconda3/envs/tiktok/bin/python RESTART_DELAY=15 ./run_forever.sh
```

历史数据回填（补齐 `work_info`）：

```bash
python3 backfill_work_info.py
python3 backfill_work_info.py --save-every 20 --sleep 0.3
python3 backfill_work_info.py --refresh --limit 100
```

日志文件：
- `logs/runner.log`：守护脚本启动/重启记录
- `logs/app2.log`：`main.py` 标准输出和错误输出
- `logs/download_manifest.jsonl`：下载结果清单（每行一条 JSON）

## 输出格式

`scraped_data.json`：

```json
[
  {
    "url": "https://v.douyin.com/xxxxx/",
    "canonical_url": "https://www.douyin.com/video/7623xxxx",
    "aweme_id": "7623xxxx",
    "title": "视频描述文字",
    "author": "用户名",
    "platform": "抖音",
    "raw_text": "完整分享文本（含话题标签）",
    "download_status": "success",
    "download_path": "/Users/xxx/DouYin_Spider/datas/media_datas/...",
    "download_error": "",
    "downloaded_at": "2026-04-02 14:52:21",
    "work_info": {
      "work_id": "7623xxxx",
      "work_url": "https://www.douyin.com/video/7623xxxx",
      "create_time_str": "2026-04-02 14:52:21",
      "digg_count": 1234,
      "comment_count": 45,
      "share_count": 12,
      "nickname": "作者昵称"
    },
    "scraped_at": "2026-03-11 19:00:00"
  }
]
```

## 依赖

| 包 | 用途 |
|---|---|
| `playwright` | 浏览器自动化 |
| `playwright-stealth` | 反检测 |
| `streamlit` | 可选：Web UI（`python3 app.py`） |
| `pandas` | 可选：CSV 导出 |
| `yt-dlp` | 可选：抓到链接后自动下载视频 |

## 注意事项

- 首次运行需手动登录平台账号（会话保存在 `browser_data/` 目录）
- 程序会优先使用系统已安装的正式版 Google Chrome，并单独使用项目内的 `browser_data/` 作为登录会话目录
- 如 TikTok 在自动启动的浏览器里仍无法登录，可改用 `--attach-real-chrome` 连接你手动启动的真实 Chrome
- 若未检测到登录态，程序会等待一段时间让你手动登录，超时后自动停止
- 部分视频（直播间、广告）可能无法获取链接，会自动跳过
- 启用 `--download-ytdlp` 后，程序会边抓边下；`--ytdlp-archive-file` 会记录已下载条目，避免重复下载
- 请合理控制抓取频率，遵守平台使用规则

## 项目结构

```
.
├── main.py              # 命令行入口
├── app.py               # Streamlit Web UI（可选）
├── config.py            # 配置参数
├── requirements.txt
└── scraper/
    ├── browser.py       # 浏览器管理（持久化上下文 + 反指纹）
    ├── crawler.py       # 核心抓取逻辑
    └── models.py        # 数据模型
```
