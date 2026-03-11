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
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Set, Tuple

from playwright.async_api import Page, TimeoutError as PWTimeoutError

import config
from scraper.browser import create_stealth_page, close_browser
from scraper.models import VideoItem


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
        new_items.append(VideoItem(url=href, title=title, author=author, platform=cfg["name"]))
        seen_urls.add(href)

    return new_items


# ──────────────────────────────────────────────
# 5. 主抓取循环
# ──────────────────────────────────────────────

async def run_scraping_session(
    platform: str,
    max_items: int,
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
      滚动 + <a> 标签扫描
    """
    cfg = config.PLATFORM_CONFIG[platform]
    all_items: List[VideoItem] = []
    seen_urls: Set[str] = set()
    json_path = Path("scraped_data.json")

    page = await create_stealth_page()

    try:
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

        # ── 导航到「推荐」频道 ──
        if platform == "douyin":
            await _notify(on_new_items, [], "🔍 正在点击「推荐」频道…")
            found = await navigate_to_recommended(page)
            msg = "✅ 已进入「推荐」频道" if found else "ℹ️ 未找到「推荐」Tab，继续"
            await _notify(on_new_items, [], msg)
            await asyncio.sleep(3)

        no_new_rounds = 0

        while len(all_items) < max_items:
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

            # ── 抖音：V 键 → 剪贴板 → 提取链接 ──
            if platform == "douyin":
                url, raw_text = await get_video_url_via_clipboard(page)

                if url and url not in seen_urls:
                    seen_urls.add(url)
                    meta = await get_video_meta_from_dom(page)
                    title = " ".join((meta.get("title") or "（无描述）").split())
                    author = meta.get("author") or "未知"

                    item = VideoItem(
                        url=url,
                        title=title,
                        author=author,
                        platform=cfg["name"],
                        raw_text=raw_text,
                    )
                    all_items.append(item)

                    # 增量写入 JSON 文件
                    try:
                        json_path.write_text(
                            json_mod.dumps(
                                [i.to_json_dict() for i in all_items],
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                    except Exception as e:
                        print(f"[JSON] 写入失败: {e}")

                    no_new_rounds = 0
                    await _notify(on_new_items, all_items,
                                  f"🎥 已找到 {len(all_items)} 条视频（{url}）")
                else:
                    no_new_rounds += 1
                    reason = "（已抓取过）" if url and url in seen_urls else "（V 键未返回有效链接）"
                    await _notify(on_new_items, all_items,
                                  f"⏳ 切换视频中…已抓取 {len(all_items)} 条（连续 {no_new_rounds} 轮无新内容 {reason}）")

                # 随机等待后按 ArrowDown 切换
                await asyncio.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(1.5)  # 等下一个视频稳定

            else:
                # TikTok：滚动 + <a> 扫描
                total = random.randint(config.SCROLL_STEP_MIN, config.SCROLL_STEP_MAX)
                for _ in range(config.SCROLL_STEP_COUNT):
                    await page.mouse.wheel(0, total // config.SCROLL_STEP_COUNT + random.randint(-30, 30))
                    await asyncio.sleep(random.uniform(0.05, 0.2))
                await asyncio.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))

                new_items = await extract_video_items(page, platform, seen_urls)
                if new_items:
                    all_items.extend(new_items)
                    no_new_rounds = 0
                    await _notify(on_new_items, all_items,
                                  f"🎥 已找到 {len(all_items)} 条视频（本轮新增 {len(new_items)} 条）")
                else:
                    no_new_rounds += 1
                    await _notify(on_new_items, all_items,
                                  f"⏳ 滚动中…已抓取 {len(all_items)} 条（连续 {no_new_rounds} 轮无新内容）")

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
