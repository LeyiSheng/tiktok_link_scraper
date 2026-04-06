from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional


class YtDlpDownloadBridge:
    def __init__(
        self,
        ytdlp_bin: str = "yt-dlp",
        output_dir: str = "downloads",
        format_selector: str = "bv*+ba/b",
        merge_format: str = "mp4",
        archive_file: Optional[str] = None,
        cookies_from_browser: str = "",
        cookies_file: str = "",
    ):
        self.ytdlp_bin = ytdlp_bin
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.format_selector = format_selector
        self.merge_format = merge_format
        self.archive_file = (
            Path(archive_file).expanduser().resolve()
            if archive_file
            else self.output_dir / "yt_dlp_downloaded.txt"
        )
        self.cookies_from_browser = (cookies_from_browser or "").strip()
        self.cookies_file = (cookies_file or "").strip()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.archive_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_binary()

    def _ensure_binary(self) -> None:
        try:
            res = subprocess.run(
                [self.ytdlp_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"未找到 yt-dlp 可执行文件: {self.ytdlp_bin}。"
                "请先安装 yt-dlp，或通过 --ytdlp-bin 指定路径。"
            ) from exc

        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"yt-dlp 检查失败（code={res.returncode}）: {err}")

    def download(self, url: str) -> str:
        # 仅使用标题命名（不带视频 ID）
        template = str(self.output_dir / "%(title).180B.%(ext)s")
        format_candidates = []
        for fmt in [self.format_selector, "bestvideo*+bestaudio/best", "best"]:
            if fmt and fmt not in format_candidates:
                format_candidates.append(fmt)

        has_auth = bool(self.cookies_from_browser or self.cookies_file)
        extractor_args_candidates = []
        if has_auth:
            extractor_args_candidates.extend(
                [
                    "youtube:player_client=web,web_safari,android",
                    "youtube:player_client=web,android",
                    "youtube:player_client=android,web",
                ]
            )
        else:
            extractor_args_candidates.extend(
                [
                    "youtube:player_client=android,web",
                    "youtube:player_client=web,android",
                ]
            )
        extractor_args_candidates.append("")

        last_err = ""
        attempts = []
        for fmt in format_candidates:
            for extractor_args in extractor_args_candidates:
                attempts.append((fmt, extractor_args))
        for extractor_args in extractor_args_candidates:
            attempts.append(("", extractor_args))

        for idx, (fmt, extractor_args) in enumerate(attempts):
            cmd = [
                self.ytdlp_bin,
                "--newline",
                "--no-progress",
                "--restrict-filenames",
                "--no-warnings",
                "--sleep-requests",
                "1",
                "--retries",
                "6",
                "--fragment-retries",
                "6",
                "--extractor-retries",
                "3",
                "--concurrent-fragments",
                "1",
                "--download-archive",
                str(self.archive_file),
                "--print",
                "after_move:filepath",
                "-o",
                template,
            ]
            if fmt:
                cmd.extend(["-f", fmt])
            if self.merge_format:
                cmd.extend(["--merge-output-format", self.merge_format])
            if extractor_args:
                cmd.extend(["--extractor-args", extractor_args])
            if self.cookies_from_browser:
                cmd.extend(["--cookies-from-browser", self.cookies_from_browser])
            if self.cookies_file:
                cmd.extend(["--cookies", str(Path(self.cookies_file).expanduser().resolve())])
            cmd.append(url)

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            if res.returncode == 0:
                lines = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
                if lines:
                    return lines[-1]
                return str(self.output_dir)

            err = (res.stderr or res.stdout or "").strip()
            last_err = err
            lowered = err.lower()
            is_format_issue = "requested format is not available" in lowered
            is_transient_guard = any(
                marker in lowered
                for marker in [
                    "sign in to confirm you're not a bot",
                    "http error 429",
                    "too many requests",
                    "temporarily unavailable",
                    "timed out",
                    "remote end closed connection",
                ]
            )
            if is_transient_guard and idx < len(attempts) - 1:
                time.sleep(2)
                continue
            if not is_format_issue:
                break

        if len(last_err) > 800:
            last_err = last_err[:800] + "..."
        raise RuntimeError(last_err or "yt-dlp 下载失败")
