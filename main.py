"""
main.py — 命令行启动脚本

用法：
    python3 main.py                    # 默认抓取抖音，最多 50 条
    python3 main.py --max 100          # 最多 100 条
    python3 main.py --platform tiktok  # 切换到 TikTok
    python3 main.py --platform youtube # 切换到 YouTube Shorts
    python3 main.py --platform youtube --download-ytdlp  # 抓取并自动下载 Shorts 视频
    python3 main.py --output my.json   # 自定义输出文件名

结果实时保存到 scraped_data.json（或 --output 指定的文件）。
"""

import argparse
import asyncio
import json
import subprocess
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import config
from scraper.browser import close_browser, create_stealth_page
from scraper.crawler import handle_popups, is_logged_in, run_scraping_session, write_items_to_json
from scraper.douyin_downloader import DouyinDownloadBridge
from scraper.models import VideoItem, extract_video_id_from_url
from scraper.ytdlp_downloader import YtDlpDownloadBridge


DEFAULT_OUTPUT = "scraped_data.json"
TIKTOK_DEFAULT_OUTPUT = "tiktok_scraped_data.json"
YOUTUBE_DEFAULT_OUTPUT = "youtube_shorts_scraped_data.json"
DEFAULT_RSYNC_CMD = (
    "rsync -avz /Users/tailab/Desktop/DouYin_Spider/datas "
    "leyi@10.120.17.96:/data_sde/leyi/douyin_data/"
)
DEFAULT_RSYNC_STATE_FILE = "logs/rsync_state.json"
DEFAULT_YTDLP_ARCHIVE_FILE = "logs/yt_dlp_downloaded.txt"
DEFAULT_DOWNLOAD_MANIFEST_FILE = "logs/download_manifest.jsonl"


def parse_args():
    parser = argparse.ArgumentParser(description="抖音/TikTok/YouTube Shorts 视频链接抓取工具")
    parser.add_argument(
        "--platform",
        choices=["douyin", "tiktok", "youtube"],
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
        default=DEFAULT_OUTPUT,
        help=f"输出 JSON 文件路径（默认: {DEFAULT_OUTPUT}）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无界面模式（默认: 显示浏览器）",
    )
    parser.add_argument(
        "--debug-only",
        action="store_true",
        help="仅打开平台页面用于手动调试，不执行抓取动作",
    )
    parser.add_argument(
        "--attach-real-chrome",
        action="store_true",
        help="连接到手动启动并开启远程调试端口的真实 Chrome",
    )
    parser.add_argument(
        "--chrome-debug-port",
        type=int,
        default=config.CHROME_DEBUG_PORT,
        help=f"真实 Chrome 的远程调试端口（默认: {config.CHROME_DEBUG_PORT}）",
    )
    parser.add_argument(
        "--download-douyin",
        action="store_true",
        help="抓到新的抖音分享链接后，立即调用 DouYin_Spider 下载对应作品",
    )
    parser.add_argument(
        "--download-ytdlp",
        action="store_true",
        help="抓到新视频后，使用 yt-dlp 自动下载（建议用于 youtube / tiktok）",
    )
    parser.add_argument(
        "--ytdlp-bin",
        default="yt-dlp",
        help="yt-dlp 可执行文件路径（默认: yt-dlp）",
    )
    parser.add_argument(
        "--ytdlp-output-dir",
        default="downloads",
        help="yt-dlp 下载目录（默认: downloads）",
    )
    parser.add_argument(
        "--ytdlp-format",
        default="bv*+ba/b",
        help="yt-dlp 格式选择器（默认: bv*+ba/b）",
    )
    parser.add_argument(
        "--ytdlp-merge-format",
        default="mp4",
        help="yt-dlp 合并后封装格式（默认: mp4）",
    )
    parser.add_argument(
        "--ytdlp-archive-file",
        default=DEFAULT_YTDLP_ARCHIVE_FILE,
        help="yt-dlp 下载归档文件（避免重复下载）",
    )
    parser.add_argument(
        "--ytdlp-cookies-from-browser",
        default="",
        help="传给 yt-dlp 的 --cookies-from-browser 参数，例如 chrome 或 chrome:Default",
    )
    parser.add_argument(
        "--ytdlp-cookies-file",
        default="",
        help="传给 yt-dlp 的 --cookies 文件路径（Netscape 格式）",
    )
    parser.add_argument(
        "--download-save-choice",
        default="media",
        choices=["all", "media", "media-video", "media-image", "excel"],
        help="传给 DouYin_Spider 的保存方式，默认 media",
    )
    parser.add_argument(
        "--rsync-every",
        type=int,
        default=1000,
        help="每下载多少条后执行一次 rsync（默认: 1000，设置为 0 表示关闭）",
    )
    parser.add_argument(
        "--rsync-cmd",
        default=DEFAULT_RSYNC_CMD,
        help="自动同步使用的 rsync 命令",
    )
    parser.add_argument(
        "--rsync-state-file",
        default=DEFAULT_RSYNC_STATE_FILE,
        help="保存累计下载计数的状态文件（用于跨重启累计）",
    )
    parser.add_argument(
        "--download-manifest-file",
        default=DEFAULT_DOWNLOAD_MANIFEST_FILE,
        help="下载结果清单文件（JSONL，按行追加）",
    )
    return parser.parse_args()


def print_status(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


async def main(args) -> None:
    config.HEADLESS = args.headless
    config.ATTACH_REAL_CHROME = args.attach_real_chrome
    config.CHROME_DEBUG_PORT = args.chrome_debug_port
    output_arg_is_default = args.output == DEFAULT_OUTPUT
    if output_arg_is_default and args.platform == "tiktok":
        resolved_output = TIKTOK_DEFAULT_OUTPUT
    elif output_arg_is_default and args.platform == "youtube":
        resolved_output = YOUTUBE_DEFAULT_OUTPUT
    else:
        resolved_output = args.output
    output_path = Path(resolved_output)
    load_existing_history = args.platform == "douyin"
    stop_event = threading.Event()
    downloader = None
    ytdlp_downloader = None
    last_seen_count = 0
    downloaded_count = 0
    rsync_state_path = Path(args.rsync_state_file)
    download_manifest_path = Path(args.download_manifest_file)

    if args.download_douyin:
        downloader = DouyinDownloadBridge(save_choice=args.download_save_choice)
        try:
            if rsync_state_path.exists():
                state = json.loads(rsync_state_path.read_text(encoding="utf-8"))
                downloaded_count = int(state.get("downloaded_count", 0))
                print_status(f"📦 已加载累计下载计数: {downloaded_count}")
            else:
                rsync_state_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print_status(f"⚠️ 读取 rsync 计数状态失败，按 0 开始: {exc}")
            downloaded_count = 0

    if args.download_ytdlp:
        ytdlp_downloader = YtDlpDownloadBridge(
            ytdlp_bin=args.ytdlp_bin,
            output_dir=args.ytdlp_output_dir,
            format_selector=args.ytdlp_format,
            merge_format=args.ytdlp_merge_format,
            archive_file=args.ytdlp_archive_file,
            cookies_from_browser=args.ytdlp_cookies_from_browser,
            cookies_file=args.ytdlp_cookies_file,
        )

    # Ctrl+C 优雅退出
    def _sigint(sig, frame):
        print_status("⚠️  收到 Ctrl+C，正在停止…")
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)

    if args.debug_only:
        page = await create_stealth_page(args.platform)
        try:
            platform_name = (
                "抖音"
                if args.platform == "douyin"
                else "TikTok"
                if args.platform == "tiktok"
                else "YouTube Shorts"
            )
            start_url = config.PLATFORM_CONFIG[args.platform]["start_url"]
            print_status(f"🛠️ 进入 {platform_name} 调试模式，不会执行抓取动作")
            if args.attach_real_chrome:
                print_status(f"🔌 正在连接真实 Chrome，调试端口: {args.chrome_debug_port}")
            print_status(f"🌐 正在打开: {start_url}")
            print_status("（按 Ctrl+C 退出调试模式）\n")

            await page.goto(start_url, timeout=config.PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            popup_result = await handle_popups(page)
            if popup_result == "captcha":
                print_status("⚠️ 页面出现验证码，请先手动处理")
            elif popup_result == "popup_closed":
                print_status("✅ 已自动关闭初始弹窗")

            logged_in = await is_logged_in(page, args.platform)
            if logged_in:
                print_status("✅ 当前已检测到登录态")
            else:
                print_status("🔐 当前未检测到登录态，你可以先手动登录后再观察页面")

            while not stop_event.is_set():
                await asyncio.sleep(0.5)
        finally:
            await page.close()
            await close_browser()
        return

    platform_label = (
        "抖音"
        if args.platform == "douyin"
        else "TikTok"
        if args.platform == "tiktok"
        else "YouTube Shorts"
    )
    print_status(f"🚀 开始抓取 {platform_label}，目标 {args.max} 条")
    if args.attach_real_chrome:
        print_status(f"🔌 使用真实 Chrome 连接模式，调试端口: {args.chrome_debug_port}")
    print_status(f"📁 结果保存到: {output_path.resolve()}")
    if args.platform == "tiktok":
        print_status("🆕 TikTok 模式不会加载之前的保存结果，将从空文件开始写入")
    if args.platform == "youtube":
        print_status("🆕 YouTube Shorts 模式不会加载之前的保存结果，将从空文件开始写入")
    if downloader:
        print_status(f"⬇️  新抓到的抖音链接将立即下载（save_choice={args.download_save_choice}）")
    if ytdlp_downloader:
        print_status(
            "⬇️  新抓到的视频将使用 yt-dlp 自动下载"
            f"（output_dir={Path(args.ytdlp_output_dir).expanduser().resolve()}）"
        )
    print_status("（按 Ctrl+C 可随时停止）\n")

    def run_rsync(cmd: str):
        return subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )

    def persist_rsync_state(count: int):
        payload = {"downloaded_count": int(count)}
        rsync_state_path.parent.mkdir(parents=True, exist_ok=True)
        rsync_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_download_manifest(payload: dict):
        download_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with download_manifest_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _format_douyin_create_time(value) -> str:
        try:
            ts = int(value)
            if ts <= 0:
                return ""
            if ts > 10_000_000_000:
                ts = ts / 1000.0
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        except Exception:
            return ""

    def _build_work_summary(work_info: dict) -> dict:
        if not isinstance(work_info, dict):
            return {}
        return {
            "work_id": str(work_info.get("work_id", "")),
            "work_url": str(work_info.get("work_url", "")),
            "work_type": str(work_info.get("work_type", "")),
            "title": str(work_info.get("title", "")),
            "desc": str(work_info.get("desc", "")),
            "create_time": work_info.get("create_time", 0),
            "create_time_str": _format_douyin_create_time(work_info.get("create_time", 0)),
            "digg_count": work_info.get("digg_count", 0),
            "comment_count": work_info.get("comment_count", 0),
            "collect_count": work_info.get("collect_count", 0),
            "share_count": work_info.get("share_count", 0),
            "nickname": str(work_info.get("nickname", "")),
            "user_id": str(work_info.get("user_id", "")),
            "user_url": str(work_info.get("user_url", "")),
            "ip_location": str(work_info.get("ip_location", "")),
            "topics": work_info.get("topics", []) if isinstance(work_info.get("topics", []), list) else [],
            "video_addr": str(work_info.get("video_addr", "")),
            "video_cover": str(work_info.get("video_cover", "")),
        }

    async def on_new_items(items: list, message: str) -> None:
        nonlocal last_seen_count, downloaded_count
        print_status(message)

        if message.startswith("📚 已加载历史数据"):
            last_seen_count = len(items)
            return

        if len(items) <= last_seen_count:
            return

        new_items = items[last_seen_count:]
        last_seen_count = len(items)

        if downloader and args.platform == "douyin":
            for item in new_items:
                try:
                    save_path, work_info = await asyncio.to_thread(
                        downloader.download_with_work_info,
                        item.url,
                        getattr(item, "canonical_url", ""),
                    )
                    downloaded_count += 1
                    if isinstance(work_info, dict) and work_info:
                        item.work_info = _build_work_summary(work_info)
                        item.aweme_id = str(work_info.get("work_id", "")).strip() or item.aweme_id
                        item.canonical_url = str(work_info.get("work_url", "")).strip() or item.canonical_url
                        item.title = str(work_info.get("title", "")).strip() or item.title
                        item.author = str(work_info.get("nickname", "")).strip() or item.author
                    item.aweme_id = item.aweme_id or extract_video_id_from_url(item.canonical_url or item.url)
                    item.download_status = "success"
                    item.download_path = save_path
                    item.download_error = ""
                    item.downloaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await asyncio.to_thread(persist_rsync_state, downloaded_count)
                    await write_items_to_json(output_path, items)
                    await asyncio.to_thread(
                        append_download_manifest,
                        {
                            "status": "success",
                            "url": item.url,
                            "canonical_url": item.canonical_url,
                            "aweme_id": item.aweme_id,
                            "title": item.title,
                            "author": item.author,
                            "saved_path": save_path,
                            "work_info": item.work_info,
                            "downloaded_at": item.downloaded_at,
                        },
                    )
                    print_status(f"⬇️  下载完成: {item.url} -> {save_path}")
                    if args.rsync_every > 0 and downloaded_count % args.rsync_every == 0:
                        print_status(f"🔄 已下载 {downloaded_count} 条，开始执行 rsync 同步...")
                        rs = await asyncio.to_thread(run_rsync, args.rsync_cmd)
                        if rs.returncode == 0:
                            print_status("✅ rsync 同步完成")
                        else:
                            err = (rs.stderr or rs.stdout or "").strip()
                            if len(err) > 300:
                                err = err[:300] + "..."
                            print_status(f"❌ rsync 同步失败（code={rs.returncode}）: {err}")
                except Exception as exc:
                    err_msg = str(exc)
                    item.aweme_id = item.aweme_id or extract_video_id_from_url(item.canonical_url or item.url)
                    item.download_status = "failed"
                    item.download_path = ""
                    item.download_error = err_msg
                    item.downloaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await write_items_to_json(output_path, items)
                    await asyncio.to_thread(
                        append_download_manifest,
                        {
                            "status": "failed",
                            "url": item.url,
                            "canonical_url": item.canonical_url,
                            "aweme_id": item.aweme_id,
                            "title": item.title,
                            "author": item.author,
                            "error": err_msg,
                            "work_info": item.work_info,
                            "downloaded_at": item.downloaded_at,
                        },
                    )
                    print_status(f"❌ 下载失败: {item.url} ({exc})")

        if ytdlp_downloader and args.platform in {"youtube", "tiktok"}:
            for item in new_items:
                try:
                    target_url = getattr(item, "canonical_url", "") or item.url
                    save_path = await asyncio.to_thread(ytdlp_downloader.download, target_url)
                    print_status(f"⬇️  yt-dlp 下载完成: {target_url} -> {save_path}")
                except Exception as exc:
                    print_status(f"❌ yt-dlp 下载失败: {item.url} ({exc})")

    try:
        result = await run_scraping_session(
            platform=args.platform,
            max_items=args.max,
            output_path=str(output_path),
            load_existing_history=load_existing_history,
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
