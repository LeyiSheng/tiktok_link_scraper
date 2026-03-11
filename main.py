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
import json
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

import config
from scraper.browser import close_browser
from scraper.crawler import run_scraping_session
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
    return parser.parse_args()


def print_status(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


async def main(args) -> None:
    config.HEADLESS = args.headless
    output_path = Path(args.output)
    stop_event = threading.Event()

    # Ctrl+C 优雅退出
    def _sigint(sig, frame):
        print_status("⚠️  收到 Ctrl+C，正在停止…")
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)

    print_status(f"🚀 开始抓取 {'抖音' if args.platform == 'douyin' else 'TikTok'}，目标 {args.max} 条")
    print_status(f"📁 结果保存到: {output_path.resolve()}")
    print_status("（按 Ctrl+C 可随时停止）\n")

    async def on_new_items(items: list, message: str) -> None:
        print_status(message)

        # 同步写入 JSON（crawler 内部已做，这里不重复写；仅打印进度）
        # 如果用户指定了自定义输出文件，则在这里写
        if str(output_path) != "scraped_data.json" and items:
            try:
                output_path.write_text(
                    json.dumps(
                        [i.to_json_dict() for i in items],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception as e:
                print_status(f"[写入] 失败: {e}")

    try:
        result = await run_scraping_session(
            platform=args.platform,
            max_items=args.max,
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
