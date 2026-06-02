"""
短视频自动化脚本生成系统 - Streamlit Web App

输入抖音视频链接 → AI 分析视频 → 生成脚本 → 飞书文档 → 返回链接
"""
import sys
import time
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime

import streamlit as st

from config import QUALITY_PRESETS, get_quality_config, generate_doc_title
from src.douyin_extractor import DouyinExtractor, DouyinError
from src.video_analyzer import VideoAnalyzer, VideoAnalysisError
from src.script_generator import ScriptGenerator, ScriptGeneratorError
from src.feishu_ops import FeishuClient, FeishuError

# ============================================================
# 页面配置
# ============================================================

st.set_page_config(
    page_title="短视频脚本生成系统",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get help": None,
        "Report a bug": None,
        "About": None,
    },
)

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app")

# ============================================================
# Session State 初始化
# ============================================================

DEFAULTS = {
    "step": 0,              # 0=idle, 1=extracting, 2=analyzing, 3=generating, 4=feishu, 5=done
    "script_type": "auto",
    "quality": "standard",
    "video_url": "",
    "video_title": "",
    "video_author": "",
    "video_path": "",
    "synthesis": "",
    "script_json": None,
    "custom_requirements": "",
    "doc_url": "",
    "doc_id": "",
    "error": None,
    "status_msg": "",
    "generation_complete": False,
    "elapsed_start": 0.0,
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


def clear_run():
    """重置运行状态（保留用户自定义要求）."""
    saved_custom = st.session_state.get("custom_requirements", "")
    for key in DEFAULTS:
        st.session_state[key] = DEFAULTS[key]
    st.session_state.custom_requirements = saved_custom


def get_downloads_dir() -> Path:
    """获取下载目录（带 session 隔离）."""
    if "download_dir_id" not in st.session_state:
        st.session_state.download_dir_id = uuid.uuid4().hex[:8]
    downloads = Path("data") / "downloads" / st.session_state.download_dir_id
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


# ============================================================
# UI 渲染
# ============================================================

def render_header():
    """渲染页面头部."""
    st.markdown("""
    <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
        <h1>🎬 短视频脚本自动生成系统</h1>
        <p style="color:#888; font-size:0.95rem;">
            粘贴抖音链接 → AI 自动分析 → 输出飞书脚本文档
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.divider()


def render_input():
    """渲染输入区域."""
    col_url, col_type, col_quality = st.columns([3, 1, 1])

    with col_url:
        video_url = st.text_input(
            "📎 抖音视频链接",
            placeholder="https://www.douyin.com/video/... 或 https://v.douyin.com/...",
            key="input_url",
            label_visibility="visible",
        )

    with col_type:
        script_type = st.selectbox(
            "📝 脚本类型",
            options=["auto", "mix", "oral"],
            format_func=lambda x: {
                "auto": "🤖 自动检测",
                "mix": "🎞️ 混剪",
                "oral": "🎤 口播",
            }[x],
            key="input_type",
        )

    with col_quality:
        quality = st.selectbox(
            "🎯 输出质量",
            options=["fast", "standard", "fine"],
            format_func=lambda x: QUALITY_PRESETS[x]["label"],
            help="\n".join(
                f"{v['label']}: {v['description']}（{v['est_time']}）"
                for v in QUALITY_PRESETS.values()
            ),
            key="input_quality",
        )

    # 生成按钮
    can_generate = bool(video_url.strip()) and st.session_state.step in (0, 5)
    clicked = st.button("🚀 开始生成脚本", type="primary", use_container_width=True,
                        disabled=not can_generate)

    if clicked:
        clear_run()
        # 自动保存用户自定义要求（无需额外点"应用"）
        if "custom_req_input" in st.session_state and st.session_state.custom_req_input.strip():
            st.session_state.custom_requirements = st.session_state.custom_req_input
        st.session_state.video_url = video_url.strip()
        st.session_state.script_type = script_type
        st.session_state.quality = quality
        st.session_state.step = 1
        st.session_state.elapsed_start = time.time()
        st.rerun()


def render_custom_requirements():
    """渲染用户自定义要求输入区（手机友好，大白话即可）."""
    from config import load_requirements

    with st.expander("✏️ 自定义要求（可选，展开填写）", expanded=False):
        # 显示当前默认规则摘要
        req = load_requirements()
        col_a, col_b = st.columns(2)
        with col_a:
            m = req.get("混剪", {})
            ad = m.get("广告", {})
            st.caption(f"🎞️ 混剪默认：{m.get('标题字数','')} | {m.get('行数范围',[10,16])}行 | 广告：{ad.get('品牌','')}")
        with col_b:
            o = req.get("口播", {})
            st.caption(f"🎤 口播默认：{o.get('标题字数','')} | {o.get('对话轮数','')}轮")

        st.caption("在下方用大白话写下你的要求，AI 会自动将其与默认规则合并（冲突时以你的要求为准）。")

        current_val = st.session_state.get("custom_requirements", "")

        custom = st.text_area(
            "你的要求（自然语言即可，无需任何格式）",
            value=current_val,
            height=100,
            placeholder="例如：标题短一点10字左右，只要8行，不要广告，素材多用猫咪表情包，语气轻松活泼",
            key="custom_req_input",
            label_visibility="collapsed",
        )

        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("✅ 应用", use_container_width=True):
                st.session_state.custom_requirements = custom
                st.success("已保存！下次生成脚本时生效。")
        with col2:
            if custom != current_val:
                st.caption("💡 修改后请点「应用」保存")
            elif current_val:
                st.caption(f"✅ 当前生效：{current_val[:50]}{'...' if len(current_val)>50 else ''}")



def render_status():
    """渲染运行状态."""
    if st.session_state.step == 0:
        return

    st.divider()

    status_msg = st.session_state.status_msg
    if status_msg:
        # 计算已用时间
        if st.session_state.elapsed_start > 0:
            elapsed = time.time() - st.session_state.elapsed_start
            elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒" if elapsed >= 60 else f"{int(elapsed)}秒"
        else:
            elapsed_str = "..."

        preset = get_quality_config(st.session_state.quality)
        st.info(f"⏳ {status_msg}（已用时 {elapsed_str}，预计 {preset['est_time']}）")
    elif st.session_state.step == 5:
        st.success("✅ 全部完成！")


def render_result():
    """渲染结果区域."""
    if not st.session_state.generation_complete:
        return

    st.divider()

    doc_url = st.session_state.doc_url
    script_json = st.session_state.script_json

    # 脚本标题
    if script_json:
        title = script_json.get("title", "")
        st.markdown(f"### 📝 {title}")

    # 飞书文档链接卡片
    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, #f0f7ff 0%, #e8f4fd 100%);
        border: 1px solid #4a90d9;
        border-radius: 12px;
        padding: 24px;
        margin: 16px 0;
    ">
        <p style="font-size: 1.1em; margin: 0 0 8px 0;">📎 <strong>飞书脚本文档</strong></p>
        <a href="{doc_url}" target="_blank" style="
            font-size: 1em;
            color: #4a90d9;
            word-break: break-all;
            text-decoration: none;
        ">{doc_url}</a>
        <p style="margin: 16px 0 0 0; color: #666; font-size: 0.9em; line-height: 1.6;">
            💡 <strong>使用提示：</strong>打开链接后，点击右上角「<strong>…</strong>」→「<strong>创建副本</strong>」<br>
            即可将文档复制到自己的飞书账户中自由编辑使用。
        </p>
    </div>
    """, unsafe_allow_html=True)


def render_error():
    """渲染错误信息."""
    if not st.session_state.error:
        return

    st.error(f"❌ {st.session_state.error}")
    st.info("请检查视频链接是否有效，或尝试更换视频后重试。")
    if st.button("🔄 清除错误，重新开始", use_container_width=True):
        clear_run()
        st.rerun()


# ============================================================
# 管道步骤
# ============================================================

def step1_extract():
    """Step 1: 提取抖音视频."""
    video_url = st.session_state.video_url
    st.session_state.status_msg = "正在获取抖音视频..."

    try:
        extractor = DouyinExtractor()
        downloads = get_downloads_dir()
        result = extractor.extract(video_url, str(downloads))

        st.session_state.video_path = result["video_path"]
        st.session_state.video_title = result["title"]
        st.session_state.video_author = result["author"]
        st.session_state.step = 2
        st.session_state.status_msg = f"视频已就绪：{result['title'][:30]}"
        logger.info(f"视频提取成功: {result['title'][:30]} (作者: {result['author']})")
    except DouyinError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error(f"视频提取失败: {e}")


def step2_analyze():
    """Step 2: AI 分析视频（含音频转录）."""
    video_path = st.session_state.video_path
    video_title = st.session_state.video_title
    video_author = st.session_state.video_author
    quality = st.session_state.quality
    preset = get_quality_config(quality)

    st.session_state.status_msg = f"正在分析视频（{preset['label']}模式）..."

    with st.status(f"🔬 AI 分析中 — {preset['label']}模式", expanded=True) as status:
        def frame_progress(msg: str):
            status.write(msg)

        try:
            analyzer = VideoAnalyzer()
            result = analyzer.analyze(
                video_path, video_title, video_author,
                quality=quality,
                progress_callback=frame_progress,
            )

            frame_count = len(result.get("frame_analysis", []))
            audio_text = result.get("audio_transcript", "")
            audio_status = "✅ 含语音转文字" if audio_text and "转录文字" in audio_text else ""

            status.write(f"分析完成：{frame_count} 帧已分析 {audio_status}")
            status.update(label="AI 分析完成 ✅", state="complete")

            st.session_state.synthesis = result["synthesis"]
            st.session_state.step = 3
            st.session_state.status_msg = f"分析完成：{frame_count} 帧 {audio_status}"
            logger.info(f"AI 分析完成: {frame_count} 帧")
        except VideoAnalysisError as e:
            st.session_state.error = str(e)
            st.session_state.step = 0
            st.session_state.status_msg = ""
            logger.error(f"视频分析失败: {e}")
        except Exception as e:
            st.session_state.error = f"视频分析异常: {e}"
            st.session_state.step = 0
            st.session_state.status_msg = ""
            logger.error(f"视频分析异常: {e}")


def step3_generate():
    """Step 3: 生成脚本."""
    synthesis = st.session_state.synthesis
    video_title = st.session_state.video_title
    script_type = st.session_state.script_type

    st.session_state.status_msg = "正在生成脚本..."

    try:
        gen = ScriptGenerator()

        # 自动检测类型
        if script_type == "auto":
            st.session_state.status_msg = "正在自动检测脚本类型..."
            detected = gen.detect_type(synthesis, video_title)
            script_type = detected
            st.session_state.script_type = detected
            type_name = "混剪" if detected == "mix" else "口播"
            logger.info(f"检测结果: {type_name}脚本")

        custom_req = st.session_state.get("custom_requirements", "")
        script = gen.generate(synthesis, video_title, script_type, custom_req)

        st.session_state.script_json = script
        st.session_state.step = 4

        type_name = "混剪" if script_type == "mix" else "口播"
        row_count = len(script.get("rows", script.get("dialogs", [])))
        st.session_state.status_msg = f"{type_name}脚本已生成（{script.get('title', '')}）"
        logger.info(f"脚本生成成功: {script.get('title', '')}, 共{row_count}条")
    except ScriptGeneratorError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error(f"脚本生成失败: {e}")


def step4_feishu():
    """Step 4: 创建飞书文档并填入内容."""
    script = st.session_state.script_json
    script_type = st.session_state.script_type
    video_url = st.session_state.video_url
    video_title = st.session_state.video_title

    st.session_state.status_msg = "正在创建飞书文档..."

    try:
        client = FeishuClient()
        result = client.create_and_fill(
            script_type, script,
            video_url, video_title,
        )

        st.session_state.doc_id = result["doc_id"]
        st.session_state.doc_url = result["url"]
        st.session_state.step = 5
        st.session_state.generation_complete = True
        st.session_state.status_msg = ""

        logger.info(f"飞书文档已创建: {result['url']}")
    except FeishuError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error(f"飞书操作失败: {e}")


# ============================================================
# 主函数
# ============================================================

def main():
    render_header()

    # 错误显示
    render_error()

    render_input()

    # 仅空闲时显示要求查看区
    if st.session_state.step in (0, 5):
        render_custom_requirements()

    # 执行步骤（在 rerun 循环中推进）
    if st.session_state.step == 1:
        step1_extract()
        st.rerun()
    elif st.session_state.step == 2:
        step2_analyze()
        st.rerun()
    elif st.session_state.step == 3:
        step3_generate()
        st.rerun()
    elif st.session_state.step == 4:
        step4_feishu()
        st.rerun()

    render_status()
    render_result()

    # 完成后显示"重新开始"按钮
    if st.session_state.generation_complete:
        st.divider()
        if st.button("🔄 生成新脚本", type="secondary", use_container_width=True):
            clear_run()
            st.rerun()


if __name__ == "__main__":
    import os, sys, subprocess
    # 当被 python3 直接调用时（如 ModelScope 创空间），自动启动 Streamlit 服务器
    # 如果已在 Streamlit 环境中运行，则执行 main()
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        if ctx is not None:
            main()
        else:
            raise RuntimeError("Not in Streamlit context")
    except Exception:
        port = int(os.environ.get("PORT", 8501))
        print(f"Launching Streamlit on port {port}...")
        subprocess.run([sys.executable, "-m", "streamlit", "run", __file__,
                        "--server.port", str(port),
                        "--server.address", "0.0.0.0",
                        "--server.headless", "true"])
