"""
短视频自动化脚本生成系统 - Streamlit Web App

用户输入抖音视频链接 → AI 分析视频 → 生成脚本 → 飞书文档 → 返回链接
"""
import sys
import time
import json
import uuid
import logging
import traceback
from pathlib import Path
from datetime import datetime

import streamlit as st

from config import generate_doc_title
from douyin_extractor import DouyinExtractor, DouyinError
from video_analyzer import VideoAnalyzer, VideoAnalysisError
from script_generator import ScriptGenerator, ScriptGeneratorError
from feishu_ops import FeishuClient, FeishuError

# ============================================================
# 页面配置
# ============================================================

st.set_page_config(
    page_title="短视频脚本生成系统",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
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
    "step": 0,           # 0=idle, 1=extracting, 2=analyzing, 3=generating, 4=feishu, 5=done
    "script_type": "auto",
    "video_url": "",
    "video_title": "",
    "video_author": "",
    "video_path": "",
    "frame_analysis": None,
    "synthesis": "",
    "script_json": None,
    "doc_url": "",
    "doc_id": "",
    "error": None,
    "run_log": [],
    "generation_complete": False,
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ============================================================
# 工具函数
# ============================================================

def add_log(msg: str):
    """添加日志到 session state."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.run_log.append(f"[{timestamp}] {msg}")

def clear_run():
    """重置运行状态."""
    for key in DEFAULTS:
        st.session_state[key] = DEFAULTS[key]

def get_downloads_dir() -> Path:
    """获取下载目录（带 session 隔离）."""
    # 使用简单的 UUID 做 session 隔离
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
    col1, col2 = st.columns([1, 5])
    with col1:
        st.markdown("# 🎬")
    with col2:
        st.title("短视频自动化脚本生成系统")
    st.caption("输入抖音视频链接 → AI 自动分析视频内容 → 生成飞书脚本文档 → 返回可分享链接")
    st.divider()

def render_input():
    """渲染输入区域."""
    col1, col2 = st.columns([3, 1])

    with col1:
        video_url = st.text_input(
            "📎 抖音视频链接",
            placeholder="https://www.douyin.com/video/7645623388727217435 或 https://v.douyin.com/xxxxx/",
            key="input_url",
        )

    with col2:
        script_type = st.selectbox(
            "📝 脚本类型",
            options=["auto", "mix", "oral"],
            format_func=lambda x: {
                "auto": "🤖 自动检测",
                "mix": "🎞️ 混剪脚本",
                "oral": "🎤 口播脚本",
            }[x],
            key="input_type",
        )

    # 生成按钮
    can_generate = bool(video_url.strip()) and st.session_state.step in (0, 5)
    if st.button("🚀 生成脚本", type="primary", use_container_width=True,
                 disabled=not can_generate):
        clear_run()
        st.session_state.video_url = video_url.strip()
        st.session_state.script_type = script_type
        st.session_state.step = 1
        st.rerun()

def render_progress():
    """渲染进度区域."""
    if st.session_state.step == 0:
        return

    st.divider()
    st.subheader("⏳ 进度")

    # 进度条
    progress_map = {1: 10, 2: 40, 3: 60, 4: 80, 5: 100}
    current_pct = progress_map.get(st.session_state.step, 0)
    st.progress(current_pct / 100)

    # 步骤状态
    steps = [
        (1, "提取视频信息"),
        (2, "AI 分析视频内容"),
        (3, "生成脚本"),
        (4, "创建飞书文档"),
        (5, "完成"),
    ]
    cols = st.columns(len(steps))
    for i, (step_num, label) in enumerate(steps):
        with cols[i]:
            if st.session_state.step > step_num:
                st.success(f"✅ {label}")
            elif st.session_state.step == step_num:
                st.info(f"⏳ {label}")
            else:
                st.caption(f"⬜ {label}")

    # 实时日志
    if st.session_state.run_log:
        with st.expander("📋 详细日志", expanded=True):
            recent_logs = st.session_state.run_log[-10:]
            for log_line in recent_logs:
                st.text(log_line)

def render_result():
    """渲染结果区域."""
    if not st.session_state.generation_complete:
        return

    st.divider()
    st.subheader("📄 结果")

    doc_url = st.session_state.doc_url

    st.success("✅ 脚本已生成！")

    # 飞书文档链接
    st.markdown(f"""
    <div style="
        background: #f0f8ff;
        border: 1px solid #4a90d9;
        border-radius: 10px;
        padding: 20px;
        margin: 15px 0;
    ">
        <p style="font-size: 1.1em; margin-bottom: 8px;">📎 <strong>飞书文档链接</strong></p>
        <a href="{doc_url}" target="_blank" style="font-size: 1em; word-break: break-all;">
            {doc_url}
        </a>
        <p style="margin-top: 12px; color: #666; font-size: 0.9em;">
            💡 <strong>使用提示</strong>：打开链接后，点击右上角「<strong>...</strong>」→「<strong>创建副本</strong>」即可复制到自己的飞书账户中编辑使用。
        </p>
    </div>
    """, unsafe_allow_html=True)

    # 操作按钮
    col1, col2 = st.columns(2)
    with col1:
        if st.session_state.script_json:
            # 下载脚本 JSON
            script_str = json.dumps(st.session_state.script_json, ensure_ascii=False, indent=2)
            st.download_button(
                label="📥 下载脚本 JSON",
                data=script_str,
                file_name=f"script_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True,
            )
    with col2:
        st.button("🔄 再来一个", type="secondary", use_container_width=True,
                  on_click=clear_run)

    # 预览脚本
    if st.session_state.script_json:
        with st.expander("📋 查看脚本内容"):
            st.json(st.session_state.script_json)

# ============================================================
# 管道步骤
# ============================================================

def step1_extract():
    """Step 1: 提取抖音视频."""
    video_url = st.session_state.video_url
    add_log(f"🔍 开始处理: {video_url[:60]}...")

    try:
        extractor = DouyinExtractor()
        downloads = get_downloads_dir()
        result = extractor.extract(video_url, str(downloads))

        st.session_state.video_path = result["video_path"]
        st.session_state.video_title = result["title"]
        st.session_state.video_author = result["author"]
        st.session_state.step = 2

        add_log(f"✅ 视频提取成功: {result['title'][:30]}... (作者: {result['author']})")
    except DouyinError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        add_log(f"❌ 视频提取失败: {e}")

def step2_analyze():
    """Step 2: AI 分析视频."""
    video_path = st.session_state.video_path
    video_title = st.session_state.video_title
    video_author = st.session_state.video_author

    add_log(f"🔬 开始分析视频内容（抽帧 + AI 逐帧分析 + 音频提取）...")

    # 用 st.status 替代 st.empty 避免线程问题
    with st.status("AI 正在分析视频...", expanded=True) as status:
        def frame_progress(msg: str):
            status.write(msg)

        try:
            analyzer = VideoAnalyzer()
            result = analyzer.analyze(
                video_path, video_title, video_author,
                progress_callback=frame_progress,
            )

            status.write(f"✅ AI 分析完成，共 {len(result['frame_analysis'])} 帧")
            status.update(label="AI 分析完成 ✅", state="complete")

            st.session_state.frame_analysis = result["frame_analysis"]
            st.session_state.synthesis = result["synthesis"]
            st.session_state.step = 3

            add_log(f"✅ AI 分析完成: 已分析 {len(result['frame_analysis'])} 帧，生成综合理解")
        except VideoAnalysisError as e:
            st.session_state.error = str(e)
            st.session_state.step = 0
            add_log(f"❌ 视频分析失败: {e}")
        except Exception as e:
            st.session_state.error = f"视频分析异常: {e}"
            st.session_state.step = 0
            add_log(f"❌ 视频分析异常: {e}")

def step3_generate():
    """Step 3: 生成脚本."""
    synthesis = st.session_state.synthesis
    video_title = st.session_state.video_title
    script_type = st.session_state.script_type

    add_log(f"✍️ 开始生成脚本...")

    try:
        gen = ScriptGenerator()

        # 自动检测类型
        if script_type == "auto":
            add_log("🤖 自动检测脚本类型...")
            detected = gen.detect_type(synthesis, video_title)
            script_type = detected
            st.session_state.script_type = detected
            type_name = "混剪" if detected == "mix" else "口播"
            add_log(f"📌 检测结果: {type_name}脚本")

        script = gen.generate(synthesis, video_title, script_type)

        st.session_state.script_json = script
        st.session_state.step = 4

        type_name = "混剪" if script_type == "mix" else "口播"
        row_count = len(script.get("rows", script.get("dialogs", [])))
        add_log(f"✅ {type_name}脚本生成成功: 标题={script.get('title', '')}, 共{row_count}条")
    except ScriptGeneratorError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        add_log(f"❌ 脚本生成失败: {e}")

def step4_feishu():
    """Step 4: 创建飞书文档并填入内容."""
    script = st.session_state.script_json
    script_type = st.session_state.script_type
    video_url = st.session_state.video_url
    video_title = st.session_state.video_title

    add_log(f"📄 正在创建飞书文档...")

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

        add_log(f"✅ 飞书文档已创建: {result['url']}")
    except FeishuError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        add_log(f"❌ 飞书操作失败: {e}")

# ============================================================
# 主函数
# ============================================================

def main():
    render_header()

    # 错误显示
    if st.session_state.error:
        st.error(f"❌ {st.session_state.error}")
        st.info("请修正后重试。提示：检查视频链接是否有效，或尝试换一个视频。")
        if st.button("🔄 清除错误并重试"):
            clear_run()
            st.rerun()

    render_input()

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

    render_progress()
    render_result()

    # 侧边栏 - 关于
    with st.sidebar:
        st.markdown("### 📖 关于")
        st.markdown("""
        本工具可自动将抖音视频转换为飞书脚本文档：

        1. 粘贴抖音视频链接
        2. AI 自动分析视频内容
        3. 生成混剪/口播脚本
        4. 输出飞书文档链接

        **分享提示**：收到飞书文档链接的人，点击「创建副本」即可保存到自己的账户。
        """)

        st.divider()
        st.markdown("### 🔧 脚本类型说明")
        st.markdown("""
        - **🤖 自动检测**：AI 根据视频内容自动选择
        - **🎞️ 混剪**：图文+配音，适合知识分享、干货盘点
        - **🎤 口播**：真人对话，适合剧情、采访、Vlog
        """)

        st.divider()
        st.caption(f"© 2026 | 版本 1.0")

if __name__ == "__main__":
    main()
