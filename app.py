"""
app.py — Streamlit 实时看板

线程安全设计：
  - 后台线程（Thread-1）把数据放入 _data_queue（标准 queue.Queue）
  - Streamlit 主线程每次 rerun 时从 queue 取出并合并到 session_state
  - 彻底避免从后台线程直接写 st.session_state（会触发 ScriptRunContext 警告并丢数据）
"""

import queue
import threading
from io import StringIO

import pandas as pd
import streamlit as st

import config
from scraper.browser import close_browser
from scraper.crawler import run_scraping_session
from scraper.models import VideoItem


# ──────────────────────────────────────────────
# 全局线程安全队列（后台线程写，Streamlit 主线程读）
# ──────────────────────────────────────────────
_data_queue: "queue.Queue[tuple]" = queue.Queue()   # (List[VideoItem], str)


# ──────────────────────────────────────────────
# 页面配置
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="视频链接嗅探器",
    page_icon="🎬",
    layout="wide",
)


# ──────────────────────────────────────────────
# Session State 初始化
# ──────────────────────────────────────────────
def _init_state() -> None:
    defaults = {
        "items": [],
        "status_msg": "等待开始…",
        "scraping": False,
        "_stop_event": None,
        "thread": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()


# ──────────────────────────────────────────────
# 每次 rerun 时：把队列里的数据刷入 session_state
# ──────────────────────────────────────────────
def _flush_queue() -> None:
    """把后台线程写入 _data_queue 的所有内容合并到 session_state（主线程调用）。"""
    updated = False
    while True:
        try:
            items, msg = _data_queue.get_nowait()
            st.session_state["items"] = items
            st.session_state["status_msg"] = msg
            updated = True
        except queue.Empty:
            break
    return updated

_flush_queue()   # 页面每次刷新时都先把队列清空


# ──────────────────────────────────────────────
# 后台抓取线程
# ──────────────────────────────────────────────

def _run_async_session(platform: str, max_items: int, stop_event: threading.Event) -> None:
    """在独立线程中执行异步抓取，结果放入 _data_queue（线程安全）。"""

    async def _callback(items, message: str) -> None:
        # 只往 queue 里写，不碰 st.session_state（跨线程安全）
        _data_queue.put((list(items), message))

    import asyncio

    async def _main() -> None:
        try:
            await run_scraping_session(
                platform=platform,
                max_items=max_items,
                on_new_items=_callback,
                stop_event=stop_event,
            )
        finally:
            await close_browser()
            # 抓取结束时发送信号给主线程
            _data_queue.put((st.session_state.get("items", []), "✅ 抓取结束"))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_main())
    finally:
        loop.close()


# ──────────────────────────────────────────────
# CSS 样式
# ──────────────────────────────────────────────
st.markdown(
    """
    <style>
    .hero-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #ff416c, #ff4b2b 50%, #f7971e);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .hero-sub { color: #888; font-size: 0.95rem; margin-bottom: 1.5rem; }
    .metric-card {
        background: linear-gradient(135deg, #1a1a2e, #16213e);
        border: 1px solid #0f3460;
        border-radius: 12px;
        padding: 1rem 1.5rem;
        text-align: center;
        color: white;
    }
    .metric-num { font-size: 2.5rem; font-weight: 700; color: #e94560; }
    .metric-label { font-size: 0.85rem; color: #aaa; }
    .status-bar {
        background: #1a1a2e;
        border-left: 4px solid #e94560;
        border-radius: 4px;
        padding: 0.6rem 1rem;
        font-size: 0.9rem;
        color: #eee;
        margin-bottom: 1rem;
    }
    section[data-testid="stSidebar"] { background: #0d0d1a; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ──────────────────────────────────────────────
# 侧边栏控制面板
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🎛️ 控制面板")
    st.divider()

    platform_display = st.radio(
        "目标平台",
        options=["抖音 (Douyin)", "TikTok", "YouTube Shorts"],
        index=0,
    )
    if "抖音" in platform_display:
        platform_key = "douyin"
    elif "TikTok" in platform_display:
        platform_key = "tiktok"
    else:
        platform_key = "youtube"

    max_items = st.slider(
        "最大抓取数量",
        min_value=10,
        max_value=500,
        value=config.MAX_ITEMS_DEFAULT,
        step=10,
    )

    headless_mode = st.checkbox(
        "无界面模式 (Headless)",
        value=config.HEADLESS,
        help="首次使用时建议关闭，以便手动登录",
    )
    config.HEADLESS = headless_mode

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        start_btn = st.button(
            "▶ 开始抓取",
            disabled=st.session_state["scraping"],
            use_container_width=True,
            type="primary",
        )
    with col2:
        stop_btn = st.button(
            "■ 停止",
            disabled=not st.session_state["scraping"],
            use_container_width=True,
        )

    st.divider()
    st.caption("💡 **提示**：首次运行会弹出浏览器，请手动完成登录后抓取将自动继续。")
    st.caption("⚠️ 若遇验证码，请在弹出的浏览器中手动完成后等待脚本恢复。")


# ──────────────────────────────────────────────
# 按钮逻辑
# ──────────────────────────────────────────────
if start_btn and not st.session_state["scraping"]:
    st.session_state["items"] = []
    st.session_state["status_msg"] = "🚀 正在启动浏览器…"
    st.session_state["scraping"] = True

    stop_event = threading.Event()
    st.session_state["_stop_event"] = stop_event

    thread = threading.Thread(
        target=_run_async_session,
        args=(platform_key, max_items, stop_event),
        daemon=True,
    )
    st.session_state["thread"] = thread
    thread.start()
    st.rerun()

if stop_btn and st.session_state["scraping"]:
    ev = st.session_state.get("_stop_event")
    if ev:
        ev.set()
    st.session_state["status_msg"] = "🛑 停止信号已发送，等待当前轮次结束…"
    st.rerun()


# ──────────────────────────────────────────────
# 主界面
# ──────────────────────────────────────────────
st.markdown('<div class="hero-title">🎬 视频链接自动嗅探器</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-sub">基于 Playwright Stealth · 模拟真实人类行为 · 实时可视化</div>',
    unsafe_allow_html=True,
)

# 统计卡片
col_m1, col_m2, col_m3 = st.columns(3)
items: list = st.session_state["items"]

with col_m1:
    st.markdown(
        f'<div class="metric-card"><div class="metric-num">{len(items)}</div>'
        f'<div class="metric-label">已抓取视频</div></div>',
        unsafe_allow_html=True,
    )
with col_m2:
    authors = len({i.author for i in items}) if items else 0
    st.markdown(
        f'<div class="metric-card"><div class="metric-num">{authors}</div>'
        f'<div class="metric-label">去重创作者</div></div>',
        unsafe_allow_html=True,
    )
with col_m3:
    status_icon = "🟢" if st.session_state["scraping"] else "⚫"
    status_text = "运行中" if st.session_state["scraping"] else "空闲"
    st.markdown(
        f'<div class="metric-card"><div class="metric-num">{status_icon}</div>'
        f'<div class="metric-label">状态：{status_text}</div></div>',
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

# 状态消息
st.markdown(
    f'<div class="status-bar">{st.session_state["status_msg"]}</div>',
    unsafe_allow_html=True,
)

# 数据表格
if items:
    df = pd.DataFrame([i.to_dict() for i in items])
    st.dataframe(
        df,
        use_container_width=True,
        height=min(600, 80 + len(df) * 35),
        column_config={
            "URL": st.column_config.LinkColumn("🔗 链接", display_text="打开"),
            "标题/描述": st.column_config.TextColumn("📝 标题/描述", width="large"),
            "作者": st.column_config.TextColumn("👤 作者"),
            "平台": st.column_config.TextColumn("📱 平台"),
            "抓取时间": st.column_config.TextColumn("🕐 抓取时间"),
        },
        hide_index=True,
    )

    # CSV 导出
    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False, encoding="utf-8-sig")

    dl_col1, dl_col2 = st.columns([1, 1])
    with dl_col1:
        st.download_button(
            label="⬇️ 导出 CSV",
            data=csv_buf.getvalue(),
            file_name="video_links.csv",
            mime="text/csv",
        )
    with dl_col2:
        import json as _json
        json_str = _json.dumps(
            [i.to_json_dict() for i in items],
            ensure_ascii=False,
            indent=2,
        )
        st.download_button(
            label="⬇️ 导出 JSON（含原始文本）",
            data=json_str.encode("utf-8"),
            file_name="scraped_data.json",
            mime="application/json",
        )
else:
    st.info("暂无数据。点击左侧「开始抓取」按钮启动。", icon="📭")


# ──────────────────────────────────────────────
# 自动刷新（抓取中每 2 秒 rerun 一次，同时从队列取出新数据）
# ──────────────────────────────────────────────
if st.session_state["scraping"]:
    thread = st.session_state.get("thread")
    if thread and not thread.is_alive():
        # 线程已结束，最后清一次队列
        _flush_queue()
        st.session_state["scraping"] = False
        st.rerun()
    else:
        import time
        time.sleep(2)
        _flush_queue()   # ← 关键：在 rerun 前先把队列里的数据同步到 session_state
        st.rerun()
