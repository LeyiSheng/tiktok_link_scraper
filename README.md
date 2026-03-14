# 🎬 抖音视频链接自动嗅探器

基于 Playwright 的抖音推荐流视频链接批量抓取工具。

## 工作原理

1. 打开抖音推荐页（可显示浏览器，需手动登录一次）
2. 对每个视频按 **`V` 键**（抖音内置快捷键：复制当前视频链接）
3. 通过 `navigator.clipboard.readText()` 读取剪贴板
4. 从分享文本中提取短链，按 `↓` 键切换下一个视频
5. 结果实时保存到 `scraped_data.json`

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
python3 -m playwright install chromium

# 2. 运行（首次会弹出浏览器，请手动登录抖音）
python3 main.py

# 3. 更多选项
python3 main.py --max 200           # 抓 200 条
python3 main.py --output data.json  # 自定义输出文件
python3 main.py --headless          # 无界面模式
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

日志文件：
- `logs/runner.log`：守护脚本启动/重启记录
- `logs/app.log`：`main.py` 标准输出和错误输出

## 输出格式

`scraped_data.json`：

```json
[
  {
    "url": "https://v.douyin.com/xxxxx/",
    "title": "视频描述文字",
    "author": "用户名",
    "platform": "抖音",
    "raw_text": "完整分享文本（含话题标签）",
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

## 注意事项

- 首次运行需手动登录抖音（会话保存在 `browser_data/` 目录）
- 部分视频（直播间、广告）可能无法获取链接，会自动跳过
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
