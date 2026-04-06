"""
scraper/crawler.py — 核心抓取逻辑

视频链接提取方式（最终方案）：
  抖音内置快捷键 V = 复制当前视频链接到剪贴板
  步骤：
    1. 对当前视频按 V 键
    2. 通过 navigator.clipboard.readText() 读取剪贴板
    3. 剪贴板内容即为完整视频 URL（如 https://v.douyin.com/xxxx 或 https://www.douyin.com/video/xxx）
    4. 从 DOM 补充标题和作者信息
    5. 按 ArrowDown 切换到下一个视频
"""

import asyncio
import json as json_mod
import random
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Set, Tuple

from playwright.async_api import Page, TimeoutError as PWTimeoutError

import config
from scraper.browser import create_stealth_page, close_browser
from scraper.models import VideoItem, extract_video_id_from_url


# ──────────────────────────────────────────────
# 1. 导航到「推荐」频道
# ──────────────────────────────────────────────

_RECOMMEND_TAB_SELECTORS = [
    '[data-e2e="channel-item-recommend"]',
    '[data-e2e="home-recommend"]',
    'span:text-is("推荐")',
    'a:text-is("推荐")',
    'li:text-is("推荐")',
    'a[href="/"]',
]

async def navigate_to_recommended(page: Page) -> bool:
    """点击「推荐」频道 Tab，返回是否成功。"""
    await asyncio.sleep(2)

    for selector in _RECOMMEND_TAB_SELECTORS:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=1500):
                await el.click(timeout=config.INTERACTION_TIMEOUT)
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    # JS 兜底：遍历文本节点找「推荐」
    try:
        clicked = await page.evaluate("""
            () => {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                let node;
                while ((node = walker.nextNode())) {
                    if (node.textContent.trim() === '推荐') {
                        const el = node.parentElement;
                        if (el && ['A','BUTTON','SPAN','LI'].includes(el.tagName)) {
                            el.click();
                            return true;
                        }
                    }
                }
                return false;
            }
        """)
        if clicked:
            await asyncio.sleep(1.5)
            return True
    except Exception:
        pass

    return False


# ──────────────────────────────────────────────
# 2. 弹窗处理
# ──────────────────────────────────────────────

_CLOSE_SELECTORS = [
    '[data-e2e="close-button"]',
    'button[aria-label="关闭"]',
    'button[aria-label="Close"]',
    '.modal-close', '.close-btn', '.close',
    '[data-testid="modal-close-inner-button"]',
    'button[title="Close"]',
]

_CAPTCHA_KEYWORDS = ["验证码", "captcha", "robot", "人机验证", "滑块", "verify"]

_LOGIN_REQUIRED_KEYWORDS = {
    "douyin": [
        "登录后",
        "立即登录",
        "扫码登录",
        "手机验证码登录",
    ],
    "tiktok": [
        "log in",
        "sign up",
        "continue as guest",
        "use phone / email / username",
    ],
    "youtube": [],
}

_LOGIN_READY_SELECTORS = {
    "douyin": [
        '[data-e2e="recommend-feed"]',
        '[data-e2e="video-player"]',
        'div[class*="feed"]',
    ],
    "tiktok": [
        '[data-e2e="recommend-list-item-container"]',
        '[data-e2e="browse-video-desc"]',
        'div[data-e2e="video-player"]',
        'div[class*="DivFeedContainer"]',
    ],
    "youtube": [
        "ytd-reel-video-renderer",
        "a[href*='/shorts/']",
        "ytd-rich-grid-media",
    ],
}


async def handle_popups(page: Page) -> Optional[str]:
    try:
        page_text = await page.evaluate("document.body.innerText")
    except Exception:
        return None

    if any(kw in page_text.lower() for kw in _CAPTCHA_KEYWORDS):
        return "captcha"

    for selector in _CLOSE_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=config.INTERACTION_TIMEOUT)
                await asyncio.sleep(0.5)
                return "popup_closed"
        except Exception:
            continue

    return None


async def is_logged_in(page: Page, platform: str) -> bool:
    cfg = config.PLATFORM_CONFIG[platform]

    try:
        cookies = await page.context.cookies([cfg["start_url"]])
        cookie_names = {cookie.get("name", "") for cookie in cookies}
    except Exception:
        cookie_names = set()

    if platform == "tiktok" and {"sessionid", "sessionid_ss"} & cookie_names:
        return True
    if platform == "douyin" and {"sessionid", "sessionid_ss", "sid_guard"} & cookie_names:
        return True

    try:
        body_text = (
            await page.evaluate("document.body ? document.body.innerText : ''")
        ).lower()
    except Exception:
        body_text = ""

    login_keywords = _LOGIN_REQUIRED_KEYWORDS.get(platform, [])
    has_login_hint = any(keyword.lower() in body_text for keyword in login_keywords)

    ready_selectors = _LOGIN_READY_SELECTORS.get(platform, [])
    has_ready_selector = False
    for selector in ready_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=500):
                has_ready_selector = True
                break
        except Exception:
            continue

    current_url = (page.url or "").lower()
    if "/login" in current_url or "login" in current_url and platform == "tiktok":
        return False

    return has_ready_selector and not has_login_hint


async def ensure_logged_in(
    page: Page,
    platform: str,
    on_new_items: Optional[Callable[[List[VideoItem], str], Awaitable[None]]],
    items: List[VideoItem],
) -> bool:
    if platform == "youtube":
        # YouTube Shorts 默认可未登录访问，不阻塞等待登录。
        return True

    if await is_logged_in(page, platform):
        return True

    await _notify(
        on_new_items,
        items,
        f"🔐 未检测到 {config.PLATFORM_CONFIG[platform]['name']} 登录态，请先在浏览器中完成登录，最多等待 {config.LOGIN_WAIT_TIMEOUT} 秒…",
    )

    waited = 0.0
    while waited < config.LOGIN_WAIT_TIMEOUT:
        await asyncio.sleep(config.LOGIN_CHECK_INTERVAL)
        waited += config.LOGIN_CHECK_INTERVAL

        popup_result = await handle_popups(page)
        if popup_result == "popup_closed":
            await _notify(on_new_items, items, "✅ 已自动关闭登录过程中的弹窗")

        if await is_logged_in(page, platform):
            await _notify(on_new_items, items, "✅ 已检测到登录成功，开始抓取")
            return True

    await _notify(
        on_new_items,
        items,
        f"❌ 等待 {config.LOGIN_WAIT_TIMEOUT} 秒后仍未检测到登录，已停止本次抓取。",
    )
    return False


# ──────────────────────────────────────────────
# 3. 核心：按 V 键 → 读剪贴板 → 获取视频链接
# ──────────────────────────────────────────────

async def get_video_url_via_clipboard(page: Page) -> Tuple[Optional[str], str]:
    """
    按下 V 键（抖音快捷键：复制当前视频链接），
    返回 (提取的URL或None, 完整原始剪贴板文本)。
    """
    try:
        await page.evaluate("navigator.clipboard.writeText('')")
    except Exception:
        pass

    await page.keyboard.press("v")
    await asyncio.sleep(1.2)

    clipboard_text = ""
    try:
        clipboard_text = await page.evaluate("navigator.clipboard.readText()")
        clipboard_text = (clipboard_text or "").strip()
        print(f"[V键] {repr(clipboard_text[:100]) if clipboard_text else '(empty)'}")
    except Exception as e:
        print(f"[V键] 读取失败: {e}")

    if not clipboard_text:
        return None, ""

    m = re.search(r'https?://[^\s]*douyin\.com[^\s]*', clipboard_text)
    url = m.group(0).rstrip('，。！、…') if m else None
    return url, clipboard_text


async def _clear_clipboard(page: Page) -> None:
    try:
        await page.evaluate("navigator.clipboard.writeText('')")
    except Exception:
        pass


async def _read_clipboard(page: Page, log_prefix: str) -> str:
    clipboard_text = ""
    try:
        clipboard_text = await page.evaluate("navigator.clipboard.readText()")
        clipboard_text = (clipboard_text or "").strip()
        print(f"{log_prefix} {repr(clipboard_text[:100]) if clipboard_text else '(empty)'}")
    except Exception as e:
        print(f"{log_prefix} 读取失败: {e}")
    return clipboard_text


_TIKTOK_COPY_BUTTON_SELECTORS = [
    'button:has-text("Copy link")',
    'button:has-text("Copy Link")',
    'button:has-text("Copy")',
    '[data-e2e="copy-link"]',
    '[data-e2e="share-copy"]',
    '[aria-label="Copy link"]',
    '[aria-label="Copy Link"]',
]


def _normalize_tiktok_url(url: str) -> str:
    cleaned = url.strip().rstrip('，。！、…')
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned

    query = urllib.parse.parse_qs(parsed.query)
    lang_values = query.get("lang")
    normalized_query = f"?lang={lang_values[0]}" if lang_values else ""
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), normalized_query, "")
    )


async def get_tiktok_video_url_via_clipboard(page: Page) -> Tuple[Optional[str], str]:
    """
    TikTok 分享流程：
      1. 按 V 打开分享面板
      2. 按 Enter 确认面板焦点
      3. 点击页面里的 Copy / Copy link 按钮
      4. 读取剪贴板
    """
    await _clear_clipboard(page)

    await page.keyboard.press("v")
    await asyncio.sleep(0.6)
    await page.keyboard.press("Enter")
    await asyncio.sleep(0.8)

    copy_clicked = False
    for selector in _TIKTOK_COPY_BUTTON_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=1200):
                await button.click(timeout=config.INTERACTION_TIMEOUT)
                copy_clicked = True
                break
        except Exception:
            continue

    if not copy_clicked:
        try:
            copy_clicked = await page.evaluate("""
                () => {
                    const candidates = Array.from(document.querySelectorAll('button, div[role="button"]'));
                    const button = candidates.find((el) => {
                        const text = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        return text === 'copy' || text === 'copy link';
                    });
                    if (!button) return false;
                    button.click();
                    return true;
                }
            """)
        except Exception:
            copy_clicked = False

    await asyncio.sleep(1.0 if copy_clicked else 0.5)
    clipboard_text = await _read_clipboard(page, "[TikTok复制]")

    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

    if not clipboard_text:
        return None, ""

    match = re.search(r'https?://[^\s]*tiktok\.com[^\s]*', clipboard_text, re.IGNORECASE)
    url = _normalize_tiktok_url(match.group(0)) if match else None
    return url, clipboard_text


_AWEME_PATTERNS = [
    re.compile(r"/video/(\d+)"),
    re.compile(r"/note/(\d+)"),
    re.compile(r"[?&]modal_id=(\d+)"),
    re.compile(r"/share/video/(\d+)"),
]


def _extract_aweme_id(url: str) -> str:
    for pattern in _AWEME_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return ""


def resolve_douyin_canonical_url(url: str) -> str:
    aweme_id = _extract_aweme_id(url)
    if aweme_id:
        return f"https://www.douyin.com/video/{aweme_id}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl()
    except (urllib.error.URLError, ValueError):
        return url

    aweme_id = _extract_aweme_id(final_url)
    if aweme_id:
        return f"https://www.douyin.com/video/{aweme_id}"
    return final_url or url



async def get_video_meta_from_dom(page: Page) -> dict:
    """
    从当前可见的视频页面 DOM 中提取标题和作者。
    结合 V 键获取的 URL 使用。
    """
    return await page.evaluate("""
        () => {
            // 标题/描述
            const titleSelectors = [
                '[data-e2e="video-desc"]',
                '[data-e2e="video-title"]',
                '[data-e2e="search-video-desc"]',
                'h1',
                '[class*="desc"]',
                '[class*="title"]',
            ];
            let title = '';
            for (const sel of titleSelectors) {
                const el = document.querySelector(sel);
                const t = el?.innerText?.trim();
                if (t && t.length > 1) { title = t.slice(0, 200); break; }
            }

            // 作者：优先从 profile 链接里提取用户名
            let author = '';
            for (const a of document.querySelectorAll('a')) {
                const href = a.href || '';
                let m = href.match(/\/user\/([^/?#]+)/);
                if (!m) m = href.match(/\/@([^/?#]+)/);
                if (m && m[1] && m[1].length > 0) {
                    author = m[1];
                    break;
                }
            }

            return { title, author };
        }
    """)


async def get_tiktok_video_meta_from_dom(page: Page) -> dict:
    """从 TikTok 当前可见视频区域提取标题和作者。"""
    return await page.evaluate("""
        () => {
            const titleSelectors = [
                '[data-e2e="browse-video-desc"]',
                '[data-e2e="video-desc"]',
                'h1[data-e2e="video-desc"]',
                'div[data-e2e="browse-video-desc"]',
            ];
            let title = '';
            for (const sel of titleSelectors) {
                const el = document.querySelector(sel);
                const t = el?.innerText?.trim();
                if (t && t.length > 1) {
                    title = t.slice(0, 200);
                    break;
                }
            }

            let author = '';
            for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href || '';
                const match = href.match(/tiktok\\.com\\/@([^/?#]+)/i) || href.match(/\\/@([^/?#]+)/);
                if (!match || !match[1]) continue;
                author = match[1];
                break;
            }

            return { title, author };
        }
    """)


async def write_items_to_json(json_path: Path, items: List[VideoItem]) -> None:
    try:
        json_path.write_text(
            json_mod.dumps(
                [item.to_json_dict() for item in items],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[JSON] 写入失败: {e}")


async def advance_to_next_video(page: Page, platform: str) -> None:
    await asyncio.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))
    if platform == "douyin":
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(1.5)
        return
    if platform == "youtube":
        # YouTube Shorts: 优先用 ArrowDown，若未生效再点击“下一条”按钮。
        def _shorts_id(url: str) -> str:
            m = re.search(r"/shorts/([A-Za-z0-9_-]{6,})", url or "", re.IGNORECASE)
            return m.group(1) if m else ""

        async def _changed(prev_id: str) -> bool:
            await asyncio.sleep(1.0)
            return _shorts_id(page.url) != prev_id and _shorts_id(page.url) != ""

        async def _focus_shorts_viewport() -> None:
            # 手动按键能生效通常因为播放器先获得了焦点；自动化也要显式做这一步。
            await page.bring_to_front()
            box = await page.locator("ytd-app").first.bounding_box()
            if box:
                cx = box["x"] + box["width"] * 0.5
                cy = box["y"] + box["height"] * 0.5
                await page.mouse.click(cx, cy)
            await page.evaluate(
                """() => {
                    const active =
                        document.querySelector('ytd-reel-video-renderer[is-active]') ||
                        document.querySelector('ytd-reel-video-renderer');
                    if (active) {
                        active.scrollIntoView({ block: 'center', inline: 'nearest' });
                        active.click();
                    }
                    if (document.body) document.body.focus();
                    const ae = document.activeElement;
                    if (
                        ae instanceof HTMLElement &&
                        (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable)
                    ) {
                        ae.blur?.();
                    }
                    window.focus();
                    document.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                }"""
            )

        async def _dispatch_arrowdown_js() -> None:
            await page.evaluate(
                """() => {
                    const opts = { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, which: 40, bubbles: true };
                    const targets = [document.activeElement, document.body, document.documentElement, window];
                    for (const t of targets) {
                        if (!t) continue;
                        try { t.dispatchEvent(new KeyboardEvent('keydown', opts)); } catch {}
                        try { t.dispatchEvent(new KeyboardEvent('keypress', opts)); } catch {}
                        try { t.dispatchEvent(new KeyboardEvent('keyup', opts)); } catch {}
                    }
                }"""
            )

        prev_id = _shorts_id(page.url)

        # 1) 强制聚焦到 Shorts 播放区，再按 ArrowDown
        try:
            await _focus_shorts_viewport()
        except Exception:
            pass
        try:
            await page.keyboard.press("ArrowDown")
            if await _changed(prev_id):
                print("[YouTube] ArrowDown 切换成功")
                return
        except Exception:
            pass
        try:
            await page.locator("body").press("ArrowDown")
            if await _changed(prev_id):
                print("[YouTube] body.press(ArrowDown) 切换成功")
                return
        except Exception:
            pass
        try:
            await _dispatch_arrowdown_js()
            if await _changed(prev_id):
                print("[YouTube] JS KeyboardEvent(ArrowDown) 切换成功")
                return
        except Exception:
            pass

        # 2) 按钮兜底（有时页面把按键事件吃掉）
        next_selectors = [
            'ytd-reel-player-overlay-renderer #navigation-button-down button',
            '#navigation-button-down button',
            'button[aria-label="Next video"]',
            'button[aria-label*="Next"]',
            'button[aria-label*="下一个"]',
        ]
        for sel in next_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=700):
                    await btn.click(timeout=config.INTERACTION_TIMEOUT)
                    if await _changed(prev_id):
                        print(f"[YouTube] 点击下一条按钮切换成功: {sel}")
                        return
            except Exception:
                continue

        print("[YouTube] ArrowDown/按钮均未切换，使用滚轮兜底")

    try:
        await page.mouse.wheel(0, random.randint(850, 1100))
    except Exception:
        await page.keyboard.press("PageDown")
    await asyncio.sleep(1.8)


# ──────────────────────────────────────────────
# 4. TikTok 专用：<a> 标签扫描
# ──────────────────────────────────────────────

async def extract_video_items(
    page: Page,
    platform: str,
    seen_urls: Set[str],
) -> List[VideoItem]:
    cfg = config.PLATFORM_CONFIG[platform]
    url_pattern = re.compile(cfg["video_url_pattern"], re.IGNORECASE)
    author_pattern = re.compile(cfg["author_url_pattern"])

    raw_items = await page.evaluate(
        """(urlPatternStr) => {
            const pattern = new RegExp(urlPatternStr, 'i');
            const results = [];
            const seen = new Set();
            document.querySelectorAll('a[href]').forEach(anchor => {
                const href = anchor.href;
                if (!pattern.test(href) || seen.has(href)) return;
                seen.add(href);
                let titleEl = anchor, title = '';
                for (let i = 0; i < 6; i++) {
                    if (!titleEl) break;
                    title = titleEl.innerText?.trim() || '';
                    if (title.length > 5) break;
                    titleEl = titleEl.parentElement;
                }
                if (!title)
                    title = anchor.getAttribute('aria-label')
                         || anchor.querySelector('img')?.getAttribute('alt') || '';
                results.push({ href, title: title.slice(0, 200) });
            });
            return results;
        }""",
        url_pattern.pattern,
    )

    new_items: List[VideoItem] = []
    for raw in raw_items:
        href: str = raw["href"]
        if href in seen_urls:
            continue
        author_match = author_pattern.search(href)
        author = author_match.group(1) if author_match else "未知"
        title = " ".join(raw["title"].split()) or "（无描述）"
        new_items.append(
            VideoItem(
                url=href,
                title=title,
                author=author,
                platform=cfg["name"],
                aweme_id=extract_video_id_from_url(href),
            )
        )
        seen_urls.add(href)

    return new_items


async def extract_youtube_shorts_items(
    page: Page,
    seen_urls: Set[str],
) -> List[VideoItem]:
    """
    从当前 YouTube 页面 DOM 扫描 Shorts 链接（/shorts/<id>）。
    """
    raw_items = await page.evaluate(
        """() => {
            const out = [];
            const seen = new Set();
            const anchors = Array.from(document.querySelectorAll('a[href*="/shorts/"]'));
            for (const a of anchors) {
                const hrefRaw = a.href || a.getAttribute("href") || "";
                if (!hrefRaw) continue;
                let absHref = hrefRaw;
                try { absHref = new URL(hrefRaw, location.origin).href; } catch (_) {}
                const m = absHref.match(/https?:\\/\\/www\\.youtube\\.com\\/shorts\\/([A-Za-z0-9_-]{6,})/i);
                if (!m) continue;

                const canonical = `https://www.youtube.com/shorts/${m[1]}`;
                if (seen.has(canonical)) continue;
                seen.add(canonical);

                const title = String(
                    a.getAttribute("title")
                    || a.getAttribute("aria-label")
                    || a.textContent
                    || ""
                ).trim().slice(0, 200);

                let author = "";
                const parent = a.closest("ytd-reel-video-renderer, ytd-rich-grid-media, ytd-grid-video-renderer, ytd-video-renderer");
                if (parent) {
                    const authorAnchor = parent.querySelector('a[href^="/@"]');
                    if (authorAnchor) {
                        const ah = authorAnchor.getAttribute("href") || "";
                        const am = ah.match(/\\/@([^/?#]+)/);
                        if (am) author = am[1];
                    }
                }

                out.push({ href: canonical, title, author });
            }
            return out;
        }"""
    )

    new_items: List[VideoItem] = []
    for raw in raw_items:
        href = str(raw.get("href", "")).strip()
        if not href or href in seen_urls:
            continue
        title = " ".join(str(raw.get("title", "")).split()) or "（无描述）"
        author = str(raw.get("author", "")).strip() or "未知"
        item = VideoItem(
            url=href,
            canonical_url=href,
            aweme_id=extract_video_id_from_url(href),
            title=title,
            author=author,
            platform="YouTube Shorts",
            raw_text=href,
        )
        new_items.append(item)
        seen_urls.add(href)
    return new_items


# ──────────────────────────────────────────────
# 5. 主抓取循环
# ──────────────────────────────────────────────

async def run_scraping_session(
    platform: str,
    max_items: int,
    output_path: str = "scraped_data.json",
    load_existing_history: bool = True,
    on_new_items: Optional[Callable[[List[VideoItem], str], Awaitable[None]]] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[VideoItem]:
    """
    完整的抓取会话。

    抖音流程（每轮）：
      1. 按 V 键 → 链接写入剪贴板
      2. 读剪贴板 → 获取视频 URL
      3. 读 DOM → 补充标题和作者
      4. 按 ArrowDown → 切下一个视频

    TikTok 流程：
      1. 按 V 打开分享面板
      2. 按 Enter 确认
      3. 点击 Copy / Copy link
      4. 读剪贴板中的视频链接
      5. 滚到下一条视频

    YouTube Shorts 流程：
      1. 从 DOM 扫描 /shorts/<id> 链接
      2. 去重后落盘 JSON
      3. 下滚继续扫描
    """
    cfg = config.PLATFORM_CONFIG[platform]
    json_path = Path(output_path)
    all_items: List[VideoItem] = []
    seen_urls: Set[str] = set()

    # 可选加载历史数据，用于断点续抓和 URL 去重
    if load_existing_history and json_path.exists():
        try:
            raw = json_mod.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url", "")).strip()
                    if not url or url in seen_urls:
                        continue
                    parsed_scraped_at = str(item.get("scraped_at", "")).strip()
                    payload = dict(
                        url=url,
                        canonical_url=str(item.get("canonical_url", "")).strip(),
                        aweme_id=str(item.get("aweme_id", "")).strip(),
                        title=str(item.get("title", "（无描述）")).strip() or "（无描述）",
                        author=str(item.get("author", "未知")).strip() or "未知",
                        platform=str(item.get("platform", cfg["name"])).strip() or cfg["name"],
                        raw_text=str(item.get("raw_text", "")),
                        work_info=item.get("work_info", {}) if isinstance(item.get("work_info", {}), dict) else {},
                        download_status=str(item.get("download_status", "")).strip(),
                        download_path=str(item.get("download_path", "")).strip(),
                        download_error=str(item.get("download_error", "")).strip(),
                        downloaded_at=str(item.get("downloaded_at", "")).strip(),
                    )
                    if parsed_scraped_at:
                        payload["scraped_at"] = parsed_scraped_at
                    all_items.append(VideoItem(**payload))
                    seen_urls.add(url)
        except Exception as e:
            print(f"[JSON] 读取历史数据失败: {e}")

    page = await create_stealth_page(platform)
    existing_count = len(all_items)

    try:
        if all_items:
            await _notify(on_new_items, all_items, f"📚 已加载历史数据 {len(all_items)} 条，继续追加抓取")

        await _notify(on_new_items, [], f"📂 正在打开 {cfg['name']} 首页…")
        await page.goto(cfg["start_url"], timeout=config.PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # 初始弹窗
        popup_result = await handle_popups(page)
        if popup_result == "captcha":
            await _notify(on_new_items, [], "⚠️ 检测到验证码！请手动完成验证，等待 15 秒…")
            await asyncio.sleep(15)
        elif popup_result == "popup_closed":
            await _notify(on_new_items, [], "✅ 已自动关闭弹窗")

        if not await ensure_logged_in(page, platform, on_new_items, all_items):
            return all_items

        # ── 导航到「推荐」频道 ──
        if platform == "douyin":
            await _notify(on_new_items, [], "🔍 正在点击「推荐」频道…")
            found = await navigate_to_recommended(page)
            msg = "✅ 已进入「推荐」频道" if found else "ℹ️ 未找到「推荐」Tab，继续"
            await _notify(on_new_items, [], msg)
            await asyncio.sleep(3)

        no_new_rounds = 0
        last_copied_text: Optional[str] = None

        while (len(all_items) - existing_count) < max_items:
            if stop_event and stop_event.is_set():
                await _notify(on_new_items, all_items, "🛑 用户手动停止")
                break

            # 中途弹窗
            popup_result = await handle_popups(page)
            if popup_result == "captcha":
                await _notify(on_new_items, all_items,
                              "⚠️ 检测到验证码！请在浏览器完成验证，等待 20 秒…")
                await asyncio.sleep(20)
                continue
            elif popup_result == "popup_closed":
                await _notify(on_new_items, all_items, "✅ 自动关闭中途弹窗")

            # ── 抖音/TikTok：分享快捷键 → 剪贴板 → 提取链接 ──
            if platform in {"douyin", "tiktok"}:
                if platform == "douyin":
                    url, raw_text = await get_video_url_via_clipboard(page)
                else:
                    url, raw_text = await get_tiktok_video_url_via_clipboard(page)
                current_copied_text = raw_text.strip()

                # 连续两次复制内容相同则跳过，不保存
                if current_copied_text and current_copied_text == last_copied_text:
                    no_new_rounds += 1
                    await _notify(
                        on_new_items,
                        all_items,
                        f"⏳ 切换视频中…已抓取 {len(all_items)} 条（连续 {no_new_rounds} 轮无新内容 （复制内容与上一条相同））",
                    )
                    await advance_to_next_video(page, platform)
                    continue

                if current_copied_text:
                    last_copied_text = current_copied_text

                if url and url not in seen_urls:
                    seen_urls.add(url)
                    if platform == "douyin":
                        meta = await get_video_meta_from_dom(page)
                        canonical_url = await asyncio.to_thread(resolve_douyin_canonical_url, url)
                    else:
                        meta = await get_tiktok_video_meta_from_dom(page)
                        canonical_url = url
                    aweme_id = extract_video_id_from_url(canonical_url or url)
                    title = " ".join((meta.get("title") or "（无描述）").split())
                    author = meta.get("author") or "未知"

                    item = VideoItem(
                        url=url,
                        canonical_url=canonical_url,
                        aweme_id=aweme_id,
                        title=title,
                        author=author,
                        platform=cfg["name"],
                        raw_text=raw_text,
                    )
                    all_items.append(item)
                    await write_items_to_json(json_path, all_items)

                    no_new_rounds = 0
                    await _notify(on_new_items, all_items,
                                  f"🎥 已找到 {len(all_items)} 条视频（{url}）")
                else:
                    fallback_items: List[VideoItem] = []
                    if platform == "tiktok":
                        fallback_items = await extract_video_items(page, platform, seen_urls)
                        if fallback_items:
                            all_items.extend(fallback_items)
                            await write_items_to_json(json_path, all_items)
                            no_new_rounds = 0
                            await _notify(
                                on_new_items,
                                all_items,
                                f"🎥 已找到 {len(all_items)} 条视频（剪贴板失败，本轮兜底新增 {len(fallback_items)} 条）",
                            )
                        else:
                            no_new_rounds += 1
                            reason = "（已抓取过）" if url and url in seen_urls else "（分享复制未返回有效链接）"
                            await _notify(
                                on_new_items,
                                all_items,
                                f"⏳ 切换视频中…已抓取 {len(all_items)} 条（连续 {no_new_rounds} 轮无新内容 {reason}）",
                            )
                    else:
                        no_new_rounds += 1
                        reason = "（已抓取过）" if url and url in seen_urls else "（V 键未返回有效链接）"
                        await _notify(on_new_items, all_items,
                                      f"⏳ 切换视频中…已抓取 {len(all_items)} 条（连续 {no_new_rounds} 轮无新内容 {reason}）")

                await advance_to_next_video(page, platform)

            elif platform == "youtube":
                fresh = await extract_youtube_shorts_items(page, seen_urls)
                if fresh:
                    all_items.extend(fresh)
                    await write_items_to_json(json_path, all_items)
                    no_new_rounds = 0
                    await _notify(
                        on_new_items,
                        all_items,
                        f"🎥 已找到 {len(all_items)} 条 Shorts（本轮新增 {len(fresh)} 条）",
                    )
                else:
                    no_new_rounds += 1
                    await _notify(
                        on_new_items,
                        all_items,
                        f"⏳ 滚动中…已抓取 {len(all_items)} 条（连续 {no_new_rounds} 轮无新增）",
                    )

                await advance_to_next_video(page, platform)

            if no_new_rounds >= 15:
                await _notify(on_new_items, all_items, "📭 连续多轮无新内容，抓取结束。")
                break

        await _notify(on_new_items, all_items, f"✅ 抓取完成！共 {len(all_items)} 条视频。")

    except PWTimeoutError:
        await _notify(on_new_items, all_items, "❌ 页面加载超时，请检查网络连接。")
    except Exception as e:
        await _notify(on_new_items, all_items, f"❌ 发生错误: {e}")
    finally:
        await page.close()

    return all_items


async def _notify(
    callback: Optional[Callable[[List[VideoItem], str], Awaitable[None]]],
    items: List[VideoItem],
    message: str,
) -> None:
    if callback:
        await callback(items, message)
