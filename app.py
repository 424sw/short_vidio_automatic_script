"""
短视频脚本生成系统 - 极简版
输入抖音链接 → AI 分析 → 生成脚本 → 飞书文档
"""
import time
import logging
import importlib
from pathlib import Path

import streamlit as st

import src.douyin_extractor
import src.video_analyzer
import src.script_generator
import src.feishu_ops

# Streamlit 热重载不会刷新已导入的 src/ 模块 — 每次 rerun 强制 reload
for _mod in [src.douyin_extractor, src.video_analyzer,
             src.script_generator, src.feishu_ops]:
    importlib.reload(_mod)

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
    "audio_transcript": "",
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
    # 显示上一次运行的错误（如有）— 仅显示错误，不混杂输入表单
    if st.session_state.error:
        st.error(st.session_state.error)
        st.info("请检查视频链接是否有效，或尝试更换视频后重试。")
        if st.button("清除错误，重新开始", type="primary", use_container_width=True):
            clear_run()
            st.rerun()
        return

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
    # 显示错误（如有）
    if st.session_state.error:
        st.error(st.session_state.error)
        if st.button("清除错误，重新开始", use_container_width=True):
            clear_run()
            st.rerun()

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
# 进度面板
# ============================================================

STEP_LABELS = {
    1: ("① 提取视频", "正在下载抖音视频并提取标题/作者..."),
    2: ("② AI 分析", "正在抽帧、语音转文字、AI 综合分析..."),
    3: ("③ 生成脚本", "正在根据分析结果生成混剪脚本..."),
    4: ("④ 飞书文档", "正在创建飞书文档并填充脚本内容..."),
}

STEP_ESTIMATES = {
    1: "约 5-10 秒",
    2: "约 30-90 秒",
    3: "约 10-25 秒",
    4: "约 5-12 秒",
}

def render_progress_panel():
    step = st.session_state.step
    label, desc = STEP_LABELS.get(step, ("处理中...", ""))
    estimate = STEP_ESTIMATES.get(step, "")

    # 进度条
    st.progress((step - 1) / 4, text=f"步骤 {step}/4")

    # 步骤标题
    st.markdown(f"<h3 style='text-align:center; margin:0.75rem 0 0.25rem 0;'>{label}</h3>", unsafe_allow_html=True)
    status_placeholder = st.empty()
    status_placeholder.info(st.session_state.status_msg or desc)

    # 自定义 spinner 行：左文字 ⏳ 右预计时间
    spinner_placeholder = st.empty()
    spinner_placeholder.markdown(f"""
    <div style="display:flex; align-items:center; justify-content:space-between; padding:8px 4px;">
        <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:1.1rem; animation: spin 1.2s linear infinite; display:inline-block;">⏳</span>
            <span style="color:#555; font-size:0.95rem;">请稍候...</span>
        </div>
        <span style="color:#999; font-size:0.85rem;">⏱ {estimate}</span>
    </div>
    """, unsafe_allow_html=True)

    # 执行当前步骤
    if step == 1:
        step1_extract()
    elif step == 2:
        step2_analyze()
    elif step == 3:
        step3_generate()
    elif step == 4:
        step4_feishu()

    # 清理 spinner 占位
    spinner_placeholder.empty()

    # 步骤完成 → 推进
    if st.session_state.step != step:
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
        st.session_state.audio_transcript = audio_text
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
        audio_transcript = st.session_state.get("audio_transcript", "")
        # Whisper 默认输出繁体，转为简体中文
        if audio_transcript:
            try:
                from zhconv import convert
                audio_transcript = convert(audio_transcript, "zh-cn")
            except ImportError:
                pass
        script = gen.generate(synthesis, script_type="oral", video_title=video_title,
                              audio_transcript=audio_transcript)
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
            "oral", script,
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
    @keyframes spin { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }
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

    step = st.session_state.step

    if step == 0:
        render_input_panel()
    elif step in (1, 2, 3, 4):
        render_progress_panel()
    elif step == 5:
        render_result_panel()


if __name__ == "__main__":
    main()
