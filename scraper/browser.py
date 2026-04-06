"""
scraper/browser.py — 隐身浏览器实例管理

负责:
  1. 以持久化上下文（persistent context）启动 Chromium，保留 cookie/session
  2. 注入 playwright-stealth 以及额外的 JS 反指纹脚本
  3. 提供工厂函数 create_stealth_page() 返回已配置好的 Page
"""
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

try:
    # playwright-stealth v1.x
    from playwright_stealth import stealth_async
    _HAS_STEALTH = True
    _STEALTH_V2 = False
except ImportError:
    try:
        # playwright-stealth v2.x (API changed: uses Stealth class)
        from playwright_stealth import Stealth as _Stealth
        _HAS_STEALTH = True
        _STEALTH_V2 = True
    except ImportError:
        _HAS_STEALTH = False
        _STEALTH_V2 = False
        print("[browser] playwright-stealth 未安装，跳过 stealth 插件。")

import config

# ──────────────────────────────────────────────
# 额外的反指纹 JS（在 stealth 之外锦上添花）
# ──────────────────────────────────────────────
_ANTI_FINGERPRINT_JS = """
// 1. 覆盖 navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. 随机化 Canvas 指纹（在每个像素上加微小噪声）
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {
    const ctx = this.getContext('2d');
    if (ctx) {
        const imageData = ctx.getImageData(0, 0, this.width, this.height);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i]     ^= (Math.random() * 2) | 0;
            imageData.data[i + 1] ^= (Math.random() * 2) | 0;
            imageData.data[i + 2] ^= (Math.random() * 2) | 0;
        }
        ctx.putImageData(imageData, 0, 0);
    }
    return origToDataURL.apply(this, [type, ...args]);
};

// 3. 修复 permissions API（自动化环境默认缺失）
if (navigator.permissions) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(parameters)
    );
}

// 4. 填充 plugins，拟真实浏览器
if (navigator.plugins.length === 0) {
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ],
    });
}
"""


_playwright_instance: Optional[Playwright] = None
_browser: Optional[Browser] = None
_browser_context: Optional[BrowserContext] = None
_browser_context_platform: Optional[str] = None
_attached_to_real_chrome: bool = False

_SYSTEM_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
]


def _find_browser_executable() -> Optional[str]:
    for candidate in _SYSTEM_CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _get_storage_path(platform: str) -> Path:
    base = Path(config.BROWSER_STORAGE_DIR).resolve()
    return base / platform


def _build_launch_kwargs(platform: str, executable_path: Optional[str], storage_path: Path) -> dict:
    common = {
        "user_data_dir": str(storage_path),
        "executable_path": executable_path,
        "channel": "chrome" if executable_path and "Google Chrome.app" in executable_path else None,
        "headless": config.HEADLESS,
        "viewport": {"width": 1440, "height": 900},
        "permissions": ["clipboard-read", "clipboard-write"],
    }

    if platform == "tiktok":
        # TikTok 登录阶段更容易被“过于工整”的自动化配置触发风控。
        # 这里尽量贴近系统 Chrome 默认环境，只保留必要能力。
        common["args"] = [
            "--no-first-run",
            "--no-default-browser-check",
        ]
        return common

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
    common.update(
        {
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
            "user_agent": user_agent,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
    )
    return common


async def _get_or_create_context(platform: str = "douyin") -> BrowserContext:  # type: ignore[return]
    """获取（或首次创建）持久化浏览器上下文。"""
    global _playwright_instance, _browser, _browser_context, _browser_context_platform, _attached_to_real_chrome

    if _browser_context is not None:
        if _browser_context_platform != platform:
            print(
                f"[browser] 当前上下文基于 {_browser_context_platform} 配置启动，继续复用；"
                f"如需切到 {platform} 配置，请先重启本次程序。"
            )
        return _browser_context

    _playwright_instance = await async_playwright().start()
    if config.ATTACH_REAL_CHROME:
        endpoint = f"http://127.0.0.1:{config.CHROME_DEBUG_PORT}"
        print(f"[browser] 连接真实 Chrome: {endpoint}")
        _browser = await _playwright_instance.chromium.connect_over_cdp(endpoint)
        _attached_to_real_chrome = True
        if _browser.contexts:
            _browser_context = _browser.contexts[0]
        else:
            _browser_context = await _browser.new_context()
        _browser_context_platform = platform
    else:
        storage_path = _get_storage_path(platform)
        storage_path.mkdir(parents=True, exist_ok=True)
        print(f"[browser] 使用配置目录: {storage_path}")

        executable_path = _find_browser_executable()
        if executable_path:
            print(f"[browser] 使用系统浏览器: {executable_path}")
        else:
            print("[browser] 未找到系统 Chrome，回退到 Playwright 内置 Chromium。")

        _browser_context = await _playwright_instance.chromium.launch_persistent_context(
            **_build_launch_kwargs(platform, executable_path, storage_path),
        )
        _browser_context_platform = platform

        if platform != "tiktok":
            # 抖音保留现有的反检测增强；TikTok 登录阶段先避免额外注入。
            await _browser_context.add_init_script(_ANTI_FINGERPRINT_JS)

    # 显式授权剪贴板权限（覆盖 browser_data 可能缓存的旧设置）
    # 这是 V 键复制视频链接后能通过 JS 读取剪贴板的前提
    for origin in ("https://www.douyin.com", "https://www.tiktok.com"):
        await _browser_context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin=origin,
        )

    return _browser_context


async def create_stealth_page(platform: str = "douyin") -> Page:
    """
    创建并返回一个已应用 stealth 的新页面。

    Returns:
        playwright Page 对象（已注入反检测脚本）
    """
    ctx = await _get_or_create_context(platform)
    page = await ctx.new_page()

    # TikTok 登录阶段先避免额外 stealth 注入，降低风控触发概率。
    if _HAS_STEALTH and platform != "tiktok":
        if _STEALTH_V2:
            await _Stealth().apply_stealth_async(page)
        else:
            await stealth_async(page)

    return page


async def close_browser() -> None:
    """关闭浏览器上下文并清理资源。"""
    global _playwright_instance, _browser, _browser_context, _browser_context_platform, _attached_to_real_chrome

    if _browser_context:
        if _attached_to_real_chrome:
            _browser_context = None
            _browser_context_platform = None
        else:
            await _browser_context.close()
            _browser_context = None
            _browser_context_platform = None

    if _browser:
        await _browser.close()
        _browser = None
        _attached_to_real_chrome = False

    if _playwright_instance:
        await _playwright_instance.stop()
        _playwright_instance = None
