"""
短视频脚本生成系统 - 极简版
输入抖音链接 → AI 分析 → 生成脚本 → 飞书文档
"""
import time
import logging
from pathlib import Path

import streamlit as st

from src.douyin_extractor import DouyinExtractor, DouyinError
from src.video_analyzer import VideoAnalyzer, VideoAnalysisError
from src.script_generator import ScriptGenerator, ScriptGeneratorError
from src.feishu_ops import FeishuClient, FeishuError


st.set_page_config(
    page_title="短视频脚本生成系统",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="expanded",
    menu_items={
        "Get help": None,
        "Report a bug": None,
        "About": None,
    },
)

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("app")


# ============================================================
# 缓存资源（避免每次 rerun 都重新创建）
# ============================================================

@st.cache_resource
def _get_feishu_client():
    return FeishuClient()


# ============================================================
# Session State
# ============================================================

DEFAULTS = {
    "step": 0,
    "session_id": "",
    "video_url": "",
    "video_title": "",
    "video_author": "",
    "video_path": "",
    "synthesis": "",
    "script_json": None,
    "doc_url": "",
    "error": None,
    "status_msg": "",
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


def clear_run():
    for key in DEFAULTS:
        st.session_state[key] = DEFAULTS[key]


# ============================================================
# 输入面板
# ============================================================
def render_input_panel():
    video_url = st.text_input(
        "抖音视频链接",
        placeholder="在此处直接粘贴视频链接",
        key="input_url",
        label_visibility="visible",
    )
    clicked = st.button(
        "开始生成脚本", type="primary", use_container_width=True,
        disabled=not bool(video_url.strip()),
    )
    if clicked:
        clear_run()
        st.session_state.video_url = video_url.strip()
        st.session_state.step = 1
        st.rerun()


# ============================================================
# 结果面板
# ============================================================
def render_result_panel():
    st.success("全部完成")
    doc_url = st.session_state.get("doc_url", "")
    if doc_url:
        script = st.session_state.get("script_json") or {}
        title = script.get("title", "脚本")
        st.markdown(f"""
        <div style="border: 1px solid #d0d5dd; border-radius: 8px; padding: 20px; margin: 12px 0;">
            <p style="font-size: 1em; font-weight: 600; margin: 0 0 8px 0; color: #333;">脚本：{title}</p>
            <a href="{doc_url}" target="_blank" style="font-size: 0.9em; color: #1a56db;
                word-break: break-all; text-decoration: none;">{doc_url}</a>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.error("未生成结果。请返回重试。")
    if st.button("生成新脚本", type="secondary", use_container_width=True):
        clear_run()
        st.rerun()


# ============================================================
# 错误
# ============================================================

def render_error():
    if not st.session_state.error:
        return
    st.error(st.session_state.error)
    st.info("请检查视频链接是否有效，或尝试更换视频后重试。")
    if st.button("清除错误，重新开始", use_container_width=True):
        clear_run()
        st.rerun()


# ============================================================
# 管道步骤
# ============================================================

def step1_extract():
    st.session_state.status_msg = "正在获取抖音视频..."
    try:
        import uuid
        sid = st.session_state.get("session_id", "")
        if not sid:
            sid = uuid.uuid4().hex[:8]
            st.session_state.session_id = sid
        downloads = Path("data") / sid / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        extractor = DouyinExtractor()
        result = extractor.extract(st.session_state.video_url, str(downloads))
        st.session_state.video_path = result["video_path"]
        st.session_state.video_title = result["title"]
        st.session_state.video_author = result["author"]
        st.session_state.step = 2
        st.session_state.status_msg = f"已获取视频: {result['title'][:30]}"
        logger.info(f"视频提取成功: {result['title'][:30]}")
    except DouyinError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"视频提取失败: {e}")
    except Exception as e:
        st.session_state.error = f"视频提取异常: {e}"
        st.session_state.step = 0
        logger.exception(f"视频提取异常: {e}")


def step2_analyze():
    st.session_state.status_msg = "AI 分析中..."
    try:
        analyzer = VideoAnalyzer()
        result = analyzer.analyze(
            st.session_state.video_path,
            st.session_state.video_title,
            st.session_state.video_author,
            quality="standard",
        )
        frame_count = len(result.get("frame_analysis", []))
        audio_text = result.get("audio_transcript", "")
        audio_note = "（含语音转文字）" if audio_text and "转录" in audio_text else ""
        st.session_state.synthesis = result["synthesis"]
        st.session_state.step = 3
        st.session_state.status_msg = f"分析完成: {frame_count} 帧 {audio_note}"
        logger.info(f"AI 分析完成: {frame_count} 帧")
    except VideoAnalysisError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"视频分析失败: {e}")
    except Exception as e:
        st.session_state.error = f"视频分析异常: {e}"
        st.session_state.step = 0
        logger.exception(f"视频分析异常: {e}")


def step3_generate():
    synthesis = st.session_state.synthesis
    video_title = st.session_state.video_title
    st.session_state.status_msg = "正在生成脚本..."

    if not synthesis or not synthesis.strip():
        st.session_state.error = "视频分析结果为空，无法生成脚本。请重试。"
        st.session_state.step = 0
        return

    try:
        gen = ScriptGenerator()
        script = gen.generate(synthesis, script_type="mix", video_title=video_title)
        st.session_state.script_json = script
        st.session_state.step = 4
        st.session_state.status_msg = "脚本已生成"
        logger.info("脚本生成成功")
    except ScriptGeneratorError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"脚本生成失败: {e}")
    except Exception as e:
        st.session_state.error = f"脚本生成异常: {e}"
        st.session_state.step = 0
        logger.exception(f"脚本生成异常: {e}")


def step4_feishu():
    script = st.session_state.script_json
    st.session_state.status_msg = "正在创建飞书文档..."
    try:
        client = _get_feishu_client()
        result = client.create_and_fill(
            "mix", script,
            st.session_state.video_url, st.session_state.video_title,
            seq=1,
        )
        st.session_state.doc_url = result["url"]
        st.session_state.step = 5
        st.session_state.status_msg = ""

        video_path = st.session_state.get("video_path", "")
        if video_path and Path(video_path).exists():
            try:
                Path(video_path).unlink()
                logger.info("已清理下载视频: %s", video_path)
            except Exception:
                pass

        logger.info(f"飞书文档已创建: {result['url']}")
    except FeishuError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"飞书操作失败: {e}")
    except Exception as e:
        st.session_state.error = f"飞书操作异常: {e}"
        st.session_state.step = 0
        logger.exception(f"飞书操作异常: {e}")


# ============================================================
# 主函数
# ============================================================
def main():
    st.markdown("""
    <style>
    html, body, [data-testid="stAppViewContainer"] { overflow-y: scroll !important; }
    html { scrollbar-gutter: stable; }
    [data-testid="stToolbar"] { display: none !important; }
    footer { display: none !important; }
    .stMarkdown h1 a, .stMarkdown h2 a, .stMarkdown h3 a { display: none; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
        <h1 style="font-size:1.6rem; font-weight:700; margin-bottom:0.25rem;">短视频脚本生成系统</h1>
        <p style="color:#888; font-size:0.9rem;">
            粘贴抖音链接 &rarr; AI 分析 &rarr; 输出飞书文档
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    if st.session_state.step == 0:
        render_input_panel()

    if st.session_state.step == 5:
        render_result_panel()

    if st.session_state.step == 1:
        with st.status("正在处理...", expanded=True):
            step1_extract()
            if st.session_state.step == 2:
                st.rerun()

    elif st.session_state.step == 2:
        with st.status("正在处理...", expanded=True):
            step2_analyze()
            if st.session_state.step == 3:
                st.rerun()

    elif st.session_state.step == 3:
        with st.status("正在处理...", expanded=True):
            step3_generate()
            if st.session_state.step == 4:
                st.rerun()
            elif st.session_state.step == 0:
                st.rerun()  # 失败后刷新显示错误

    elif st.session_state.step == 4:
        with st.status("正在处理...", expanded=True):
            step4_feishu()
            if st.session_state.step == 5:
                st.rerun()
            elif st.session_state.step == 0:
                st.rerun()  # 失败后刷新显示错误

    render_error()


if __name__ == "__main__":
    main()
