"""
main.py — 命令行启动脚本

用法：
    python3 main.py                    # 默认抓取抖音，最多 50 条
    python3 main.py --max 100          # 最多 100 条
    python3 main.py --platform tiktok  # 切换到 TikTok
    python3 main.py --output my.json   # 自定义输出文件名

结果实时保存到 scraped_data.json（或 --output 指定的文件）。
"""

import argparse
import asyncio
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

import config
from scraper.browser import close_browser
from scraper.crawler import run_scraping_session
from scraper.douyin_downloader import DouyinDownloadBridge
from scraper.models import VideoItem


def parse_args():
    parser = argparse.ArgumentParser(description="抖音/TikTok 视频链接抓取工具")
    parser.add_argument(
        "--platform",
        choices=["douyin", "tiktok"],
        default="douyin",
        help="目标平台（默认: douyin）",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=config.MAX_ITEMS_DEFAULT,
        help=f"最多抓取数量（默认: {config.MAX_ITEMS_DEFAULT}）",
    )
    parser.add_argument(
        "--output",
        default="scraped_data.json",
        help="输出 JSON 文件路径（默认: scraped_data.json）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无界面模式（默认: 显示浏览器）",
    )
    parser.add_argument(
        "--download-douyin",
        action="store_true",
        help="抓到新的抖音分享链接后，立即调用 DouYin_Spider 下载对应作品",
    )
    parser.add_argument(
        "--download-save-choice",
        default="media",
        choices=["all", "media", "media-video", "media-image", "excel"],
        help="传给 DouYin_Spider 的保存方式，默认 media",
    )
    return parser.parse_args()


def print_status(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


async def main(args) -> None:
    config.HEADLESS = args.headless
    output_path = Path(args.output)
    stop_event = threading.Event()
    downloader = None
    last_seen_count = 0

    if args.download_douyin:
        downloader = DouyinDownloadBridge(save_choice=args.download_save_choice)

    # Ctrl+C 优雅退出
    def _sigint(sig, frame):
        print_status("⚠️  收到 Ctrl+C，正在停止…")
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)

    print_status(f"🚀 开始抓取 {'抖音' if args.platform == 'douyin' else 'TikTok'}，目标 {args.max} 条")
    print_status(f"📁 结果保存到: {output_path.resolve()}")
    if downloader:
        print_status(f"⬇️  新抓到的抖音链接将立即下载（save_choice={args.download_save_choice}）")
    print_status("（按 Ctrl+C 可随时停止）\n")

    async def on_new_items(items: list, message: str) -> None:
        nonlocal last_seen_count
        print_status(message)

        if not downloader or args.platform != "douyin":
            return

        if message.startswith("📚 已加载历史数据"):
            last_seen_count = len(items)
            return

        if len(items) <= last_seen_count:
            return

        new_items = items[last_seen_count:]
        last_seen_count = len(items)
        for item in new_items:
            try:
                save_path = await asyncio.to_thread(downloader.download, item.url)
                print_status(f"⬇️  下载完成: {item.url} -> {save_path}")
            except Exception as exc:
                print_status(f"❌ 下载失败: {item.url} ({exc})")

    try:
        result = await run_scraping_session(
            platform=args.platform,
            max_items=args.max,
            output_path=str(output_path),
            on_new_items=on_new_items,
            stop_event=stop_event,
        )
    finally:
        await close_browser()

    print()
    print_status(f"✅ 完成！共抓取 {len(result)} 条视频")
    print_status(f"📁 已保存到: {output_path.resolve()}")

    # 打印摘要
    if result:
        print("\n前 5 条预览：")
        for i, item in enumerate(result[:5], 1):
            print(f"  {i}. {item.url}")
            print(f"     标题: {item.title[:50]}")
            print()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
