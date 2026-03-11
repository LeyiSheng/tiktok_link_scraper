"""
config.py — 所有可调参数集中于此
修改 TARGET_PLATFORM 来切换目标平台: "douyin" 或 "tiktok"
"""

# ──────────────────────────────────────────────
# 平台选择
# ──────────────────────────────────────────────
TARGET_PLATFORM: str = "douyin"   # "douyin" | "tiktok"

# ──────────────────────────────────────────────
# 平台配置
# ──────────────────────────────────────────────
PLATFORM_CONFIG = {
    "douyin": {
        "name": "抖音",
        "start_url": "https://www.douyin.com/",
        # 视频页 URL 模式（用于正则匹配 href）
        "video_url_pattern": r"https?://www\.douyin\.com/video/\d+",
        # 用于从 href 中匹配用户名的正则（抖音个人主页路径）
        "author_url_pattern": r"/user/([^/?#]+)",
    },
    "tiktok": {
        "name": "TikTok",
        "start_url": "https://www.tiktok.com/",
        "video_url_pattern": r"https?://www\.tiktok\.com/@[^/]+/video/\d+",
        "author_url_pattern": r"/@([^/]+)/video/",
    },
}

# ──────────────────────────────────────────────
# 抓取行为
# ──────────────────────────────────────────────
MAX_ITEMS_DEFAULT: int = 50          # 默认最大抓取条数
SCROLL_PAUSE_MIN: float = 1.0        # 每次滚动后最短停顿（秒）
SCROLL_PAUSE_MAX: float = 3.0        # 每次滚动后最长停顿（秒）
SCROLL_STEP_MIN: int = 300           # 每次滚动最小距离（px）
SCROLL_STEP_MAX: int = 700           # 每次滚动最大距离（px）
SCROLL_STEP_COUNT: int = 5           # 每次"自然滚动"的微步数

# ──────────────────────────────────────────────
# 浏览器
# ──────────────────────────────────────────────
HEADLESS: bool = False               # False = 弹出可见浏览器（推荐首次运行）
BROWSER_STORAGE_DIR: str = "./browser_data"  # 持久化 cookie/session 存储路径

# ──────────────────────────────────────────────
# 超时与重试
# ──────────────────────────────────────────────
PAGE_LOAD_TIMEOUT: int = 30_000      # 页面加载超时（毫秒）
INTERACTION_TIMEOUT: int = 10_000   # 元素交互超时（毫秒）
