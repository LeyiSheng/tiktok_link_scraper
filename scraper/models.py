"""
scraper/models.py — 数据模型定义
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
import time


_VIDEO_ID_PATTERNS = [
    re.compile(r"/video/(\d+)", re.IGNORECASE),
    re.compile(r"/note/(\d+)", re.IGNORECASE),
    re.compile(r"[?&](?:modal_id|item_id|aweme_id)=([0-9A-Za-z_-]+)", re.IGNORECASE),
    re.compile(r"/shorts/([0-9A-Za-z_-]{6,})", re.IGNORECASE),
]


def extract_video_id_from_url(url: str) -> str:
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return ""


@dataclass
class VideoItem:
    """单条视频信息"""
    url: str
    title: str
    author: str
    platform: str
    canonical_url: str = ""
    aweme_id: str = ""
    raw_text: str = ""   # 抖音 V 键复制的完整分享文本（含标题、话题标签、链接）
    work_info: dict = field(default_factory=dict)  # DouYin_Spider 返回的结构化作品信息
    download_status: str = ""   # success / failed / pending / ""
    download_path: str = ""
    download_error: str = ""
    downloaded_at: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict:
        """用于 Streamlit 表格展示（不含 raw_text 以保持简洁）"""
        create_time_str = ""
        digg_count = ""
        if isinstance(self.work_info, dict) and self.work_info:
            create_time_str = str(self.work_info.get("create_time_str", "") or "")
            digg_count = self.work_info.get("digg_count", "")
            if not create_time_str:
                raw_create_time = self.work_info.get("create_time", 0)
                try:
                    ts = int(raw_create_time)
                    if ts > 10_000_000_000:
                        ts = ts / 1000.0
                    if ts > 0:
                        create_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                except Exception:
                    create_time_str = ""

        return {
            "URL": self.url,
            "完整链接": self.canonical_url or self.url,
            "视频ID": self.aweme_id,
            "标题/描述": self.title,
            "作者": self.author,
            "发布时间": create_time_str,
            "点赞": digg_count,
            "平台": self.platform,
            "下载状态": self.download_status or "N/A",
            "下载路径": self.download_path,
            "抓取时间": self.scraped_at,
        }

    def to_json_dict(self) -> dict:
        """用于 JSON 导出（包含完整原始文本）"""
        return {
            "url": self.url,
            "canonical_url": self.canonical_url,
            "aweme_id": self.aweme_id or extract_video_id_from_url(self.canonical_url or self.url),
            "title": self.title,
            "author": self.author,
            "platform": self.platform,
            "raw_text": self.raw_text,
            "download_status": self.download_status,
            "download_path": self.download_path,
            "download_error": self.download_error,
            "downloaded_at": self.downloaded_at,
            "work_info": self.work_info,
            "scraped_at": self.scraped_at,
        }
