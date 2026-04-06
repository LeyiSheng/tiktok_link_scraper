#!/usr/bin/env python3
"""
backfill_work_info.py

从 scraped_data.json 逐条回填 Douyin work_info（调用 DouYin_Spider 的 spider_work）。

默认行为：
1. 只处理抖音记录
2. 只补齐缺失 work_info 的记录
3. 每 N 条成功回填后落盘一次，避免中断丢进度

示例：
python3 backfill_work_info.py
python3 backfill_work_info.py --save-every 20 --sleep 0.3
python3 backfill_work_info.py --refresh --limit 100
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from scraper.douyin_downloader import DouyinDownloadBridge
from scraper.models import extract_video_id_from_url


DEFAULT_JSON = "scraped_data.json"
DEFAULT_STATE_FILE = "logs/backfill_work_info_state.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回填 scraped_data.json 中每条抖音视频的 work_info")
    parser.add_argument("--input", default=DEFAULT_JSON, help=f"输入 JSON 文件路径（默认: {DEFAULT_JSON}）")
    parser.add_argument("--save-every", type=int, default=10, help="每成功回填多少条就落盘一次（默认: 10）")
    parser.add_argument("--start-index", type=int, default=0, help="起始索引（默认: 0）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条（0 = 不限制）")
    parser.add_argument("--retry", type=int, default=3, help="单条失败重试次数（默认: 3）")
    parser.add_argument("--sleep", type=float, default=0.2, help="每条处理后的间隔秒数（默认: 0.2）")
    parser.add_argument("--refresh", action="store_true", help="强制刷新，即使已有 work_info 也重新拉取")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help=f"断点状态文件（默认: {DEFAULT_STATE_FILE}）")
    return parser.parse_args()


def is_douyin_item(item: dict[str, Any]) -> bool:
    platform = str(item.get("platform", "")).strip().lower()
    if platform in {"抖音", "douyin"}:
        return True
    url = f"{item.get('canonical_url', '')} {item.get('url', '')}".lower()
    return "douyin.com" in url or "v.douyin.com" in url


def _format_douyin_create_time(value: Any) -> str:
    try:
        ts = int(value)
        if ts <= 0:
            return ""
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return ""


def build_work_summary(work_info: dict[str, Any]) -> dict[str, Any]:
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


def save_json_atomic(path: Path, payload: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    state_path = Path(args.state_file).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{input_path} 不是列表 JSON")

    bridge = DouyinDownloadBridge()

    total = len(raw)
    state = load_state(state_path)
    state_index = int(state.get("next_index", 0) or 0)
    idx_start = max(0, min(max(args.start_index, state_index), total))
    changed = 0
    changed_since_save = 0
    seen = 0
    skipped_non_douyin = 0
    skipped_has_info = 0
    failed = 0

    print(f"[START] file={input_path} total={total} start_index={idx_start} state={state_path}")
    started_at = time.time()

    for idx in range(idx_start, total):
        if args.limit > 0 and seen >= args.limit:
            break
        seen += 1

        item = raw[idx]
        if not isinstance(item, dict):
            failed += 1
            save_state(
                state_path,
                {
                    "next_index": idx + 1,
                    "total": total,
                    "changed": changed,
                    "failed": failed,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            continue

        if not is_douyin_item(item):
            skipped_non_douyin += 1
            save_state(
                state_path,
                {
                    "next_index": idx + 1,
                    "total": total,
                    "changed": changed,
                    "failed": failed,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            continue

        has_work_info = isinstance(item.get("work_info"), dict) and bool(item.get("work_info"))
        if has_work_info and not args.refresh:
            skipped_has_info += 1
            save_state(
                state_path,
                {
                    "next_index": idx + 1,
                    "total": total,
                    "changed": changed,
                    "failed": failed,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            continue

        src_url = str(item.get("canonical_url", "")).strip() or str(item.get("url", "")).strip()
        if not src_url:
            item["work_info_error"] = "empty url"
            failed += 1
            save_state(
                state_path,
                {
                    "next_index": idx + 1,
                    "total": total,
                    "changed": changed,
                    "failed": failed,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            continue

        last_err = None
        work_info_raw = None
        for attempt in range(1, max(1, args.retry) + 1):
            try:
                work_info_raw = bridge.fetch_work_info(item.get("url", src_url), item.get("canonical_url", ""))
                break
            except Exception as exc:
                last_err = str(exc)
                if attempt < max(1, args.retry):
                    time.sleep(min(2.0 * attempt, 5.0))

        if not isinstance(work_info_raw, dict):
            item["work_info_error"] = last_err or "unknown error"
            item["work_info_backfilled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            failed += 1
            save_state(
                state_path,
                {
                    "next_index": idx + 1,
                    "total": total,
                    "changed": changed,
                    "failed": failed,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            if args.sleep > 0:
                time.sleep(args.sleep)
            continue

        summary = build_work_summary(work_info_raw)
        item["work_info"] = summary
        item["work_info_backfilled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        item.pop("work_info_error", None)

        item["aweme_id"] = str(item.get("aweme_id", "")).strip() or summary.get("work_id", "") or extract_video_id_from_url(src_url)
        item["canonical_url"] = str(item.get("canonical_url", "")).strip() or summary.get("work_url", "")
        item["title"] = str(summary.get("title", "")).strip() or str(item.get("title", "")).strip()

        cur_author = str(item.get("author", "")).strip()
        if not cur_author or cur_author.lower() in {"未知", "unknown", "self"}:
            item["author"] = str(summary.get("nickname", "")).strip() or cur_author

        changed += 1
        changed_since_save += 1

        if changed_since_save >= max(1, args.save_every):
            save_json_atomic(input_path, raw)
            elapsed = max(1e-6, time.time() - started_at)
            speed = changed / elapsed * 60.0
            print(
                f"[SAVE] idx={idx} changed={changed} failed={failed} "
                f"skip_has={skipped_has_info} skip_non_douyin={skipped_non_douyin} speed={speed:.1f}/min"
            )
            changed_since_save = 0

        save_state(
            state_path,
            {
                "next_index": idx + 1,
                "total": total,
                "changed": changed,
                "failed": failed,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

        if args.sleep > 0:
            time.sleep(args.sleep)

    if changed_since_save > 0:
        save_json_atomic(input_path, raw)

    elapsed = max(1e-6, time.time() - started_at)
    speed = changed / elapsed * 60.0
    print(
        f"[DONE] seen={seen} changed={changed} failed={failed} "
        f"skip_has={skipped_has_info} skip_non_douyin={skipped_non_douyin} "
        f"elapsed={elapsed:.1f}s speed={speed:.1f}/min"
    )
    save_state(
        state_path,
        {
            "next_index": total,
            "total": total,
            "changed": changed,
            "failed": failed,
            "done": True,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


if __name__ == "__main__":
    main()
