"""
scraper/browser.py — 隐身浏览器实例管理

负责:
  1. 以持久化上下文（persistent context）启动 Chromium，保留 cookie/session
  2. 注入 playwright-stealth 以及额外的 JS 反指纹脚本
  3. 提供工厂函数 create_stealth_page() 返回已配置好的 Page
"""
import asyncio
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

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
_browser_context: Optional[BrowserContext] = None


async def _get_or_create_context() -> BrowserContext:  # type: ignore[return]
    """获取（或首次创建）持久化浏览器上下文。"""
    global _playwright_instance, _browser_context

    if _browser_context is not None:
        return _browser_context

    storage_path = Path(config.BROWSER_STORAGE_DIR).resolve()
    storage_path.mkdir(parents=True, exist_ok=True)

    _playwright_instance = await async_playwright().start()

    # 真实 Chrome User-Agent
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    _browser_context = await _playwright_instance.chromium.launch_persistent_context(
        user_data_dir=str(storage_path),
        headless=config.HEADLESS,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
        ],
        user_agent=user_agent,
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        # 授权剪贴板读写权限（用于按 V 键后读取视频链接）
        permissions=["clipboard-read", "clipboard-write"],
    )

    # 注入反指纹脚本到所有未来页面
    await _browser_context.add_init_script(_ANTI_FINGERPRINT_JS)

    # 显式授权剪贴板权限（覆盖 browser_data 可能缓存的旧设置）
    # 这是 V 键复制视频链接后能通过 JS 读取剪贴板的前提
    await _browser_context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin="https://www.douyin.com",
    )

    return _browser_context


async def create_stealth_page() -> Page:
    """
    创建并返回一个已应用 stealth 的新页面。

    Returns:
        playwright Page 对象（已注入反检测脚本）
    """
    ctx = await _get_or_create_context()
    page = await ctx.new_page()

    # 应用 playwright-stealth（兼容 v1.x 和 v2.x）
    if _HAS_STEALTH:
        if _STEALTH_V2:
            await _Stealth().apply_stealth_async(page)
        else:
            await stealth_async(page)

    return page


async def close_browser() -> None:
    """关闭浏览器上下文并清理资源。"""
    global _playwright_instance, _browser_context

    if _browser_context:
        await _browser_context.close()
        _browser_context = None

    if _playwright_instance:
        await _playwright_instance.stop()
        _playwright_instance = None
