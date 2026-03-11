"""
scraper/models.py — 数据模型定义
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VideoItem:
    """单条视频信息"""
    url: str
    title: str
    author: str
    platform: str
    raw_text: str = ""   # 抖音 V 键复制的完整分享文本（含标题、话题标签、链接）
    scraped_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict:
        """用于 Streamlit 表格展示（不含 raw_text 以保持简洁）"""
        return {
            "URL": self.url,
            "标题/描述": self.title,
            "作者": self.author,
            "平台": self.platform,
            "抓取时间": self.scraped_at,
        }

    def to_json_dict(self) -> dict:
        """用于 JSON 导出（包含完整原始文本）"""
        return {
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "platform": self.platform,
            "raw_text": self.raw_text,
            "scraped_at": self.scraped_at,
        }
