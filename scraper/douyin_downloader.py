from __future__ import annotations

import sys
import threading
from pathlib import Path


DOUYIN_SPIDER_ROOT = Path(__file__).resolve().parents[2] / "DouYin_Spider"


class DouyinDownloadBridge:
    def __init__(self, project_root: Path | None = None, save_choice: str = "media"):
        self.project_root = (project_root or DOUYIN_SPIDER_ROOT).resolve()
        self.save_choice = save_choice
        self._lock = threading.Lock()
        self._initialized = False
        self._auth = None
        self._base_path = None
        self._data_spider = None

    def _ensure_ready(self) -> None:
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            if str(self.project_root) not in sys.path:
                sys.path.insert(0, str(self.project_root))

            from main import Data_Spider
            from utils.common_util import init

            self._auth, self._base_path = init()
            self._data_spider = Data_Spider()
            self._initialized = True

    def download(self, url: str) -> str:
        self._ensure_ready()

        work_info = self._data_spider.spider_work(self._auth, url)
        if self.save_choice == "all" or "media" in self.save_choice:
            from utils.data_util import download_work

            return download_work(work_info, self._base_path["media"], self.save_choice)

        if self.save_choice == "excel":
            from utils.data_util import save_to_xlsx

            file_path = self.project_root / "datas" / "excel_datas" / "single_link_import.xlsx"
            save_to_xlsx([work_info], str(file_path))
            return str(file_path)

        raise ValueError(f"不支持的 save_choice: {self.save_choice}")
