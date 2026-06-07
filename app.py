"""
短视频脚本生成系统
输入抖音链接 → AI 分析 + 类型检测 → 生成脚本 → AI 审核 → 飞书文档（5 步）
"""
import time
import uuid
import shutil
import logging
import importlib
from pathlib import Path

import streamlit as st

import config
import src.douyin_extractor
import src.video_analyzer
import src.script_generator
import src.feishu_ops
import src.prompt_builder

# Streamlit 热重载不会刷新已导入的模块 — 每次 rerun 强制 reload
# config 必须先 reload，否则 src/ 模块导入时会使用 config 旧缓存
importlib.reload(config)
for _mod in [src.douyin_extractor, src.video_analyzer,
             src.script_generator, src.feishu_ops, src.prompt_builder]:
    importlib.reload(_mod)

from src.douyin_extractor import DouyinExtractor, DouyinError  # noqa: E402
from src.video_analyzer import VideoAnalyzer, VideoAnalysisError  # noqa: E402
from src.script_generator import ScriptGenerator, ScriptGeneratorError  # noqa: E402
from src.feishu_ops import FeishuClient, FeishuError  # noqa: E402
from src.prompt_builder import build_mix_prompt, build_oral_prompt  # noqa: E402
from config import MAX_SCRIPT_COUNT, DOC_TTL_SECONDS  # noqa: E402


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
    "session_id": uuid.uuid4().hex[:8],  # 应用加载时立即生成
    "video_url": "",
    "video_title": "",
    "video_author": "",
    "video_path": "",
    "synthesis": "",
    "audio_transcript": "",
    "script_json": None,
    "script_jsons": [],
    "doc_url": "",
    "doc_urls": [],
    "created_doc_ids": [],  # 本次创建的飞书文档 ID，用于超时清理
    "doc_created_at": 0.0,  # 文档创建时间戳，用于 TTL 倒计时
    "error": None,
    "status_msg": "",
    "script_type_selection": "auto",
    "script_type": "mix",
    "quality": "standard",
    "script_count": 1,
    "target_chars": 0,
    "cancel_requested": False,
    "pipeline_started": False,  # 首次进入进度面板时显示「准备中」
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


def clear_run():
    for key in DEFAULTS:
        st.session_state[key] = DEFAULTS[key]


# ============================================================
# Session 清理
# ============================================================

def _cleanup_session(sid: str = None):
    """清理 session 临时目录。"""
    if sid is None:
        sid = st.session_state.get("session_id", "")
    if sid:
        session_dir = Path("data") / sid
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
            logger.info("已清理 session 目录: %s", session_dir)


_CLEANUP_MARKER = Path("data/.cleanup_done")


def _cleanup_stale_data():
    """启动时清理 data/ 下所有旧 session 目录。通过文件标记确保进程生命周期内只执行一次。"""
    if _CLEANUP_MARKER.exists():
        return

    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)

    # 清理所有 session 残留目录
    for item in data_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
            logger.info("启动时清理残留目录: %s", item)

    # 写入标记文件
    _CLEANUP_MARKER.write_text(str(time.time()))


# ============================================================
# 取消检查 + 浏览器存活检测
# ============================================================

class StepCancelledError(Exception):
    pass


_HEARTBEAT_STALE_SECONDS = 120  # 浏览器 2 分钟未请求 → 视为断开


def _check_cancel():
    """检查用户是否请求取消或浏览器会话已断开。

    Streamlit 每 2-3 秒 rerun 一次（进度面板期间）。
    若超过 _HEARTBEAT_STALE_SECONDS 无请求，说明浏览器已关闭。
    """
    if st.session_state.get("cancel_requested"):
        raise StepCancelledError("用户取消了生成")
    sid = st.session_state.get("session_id", "")
    if sid:
        hb = Path(f"data/{sid}/.heartbeat")
        if hb.exists() and (time.time() - hb.stat().st_mtime) > _HEARTBEAT_STALE_SECONDS:
            raise StepCancelledError("浏览器会话已断开")


def _touch_heartbeat():
    """在每个步骤开始时刷新心跳，防止长步骤被误判为浏览器断连。"""
    sid = st.session_state.get("session_id", "")
    if sid:
        hb = Path(f"data/{sid}/.heartbeat")
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.touch()


# ============================================================
# 输入面板
# ============================================================
def render_input_panel():
    # 显示上一次运行的错误（如有）— 仅显示错误，不混杂输入表单
    if st.session_state.error:
        st.error(st.session_state.error)
        # 仅视频链接相关错误才提示「检查链接」
        err_text = st.session_state.error
        is_link_issue = any(kw in err_text for kw in ("链接", "Douyin"))
        if is_link_issue:
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
    # 类型 / 质量 / 数量 同行
    col_type, col_quality, col_count = st.columns([1.5, 1, 0.7])
    with col_type:
        script_type_selection = st.selectbox(
            "脚本类型",
            options=["auto", "mix", "oral"],
            format_func=lambda x: {"auto": "自动检测", "mix": "混剪", "oral": "口播"}[x],
            index=0,
            key="script_type_selector",
        )
        st.session_state.script_type_selection = script_type_selection
    with col_quality:
        quality = st.selectbox(
            "输出质量",
            options=["standard", "fine"],
            format_func=lambda x: {"standard": "标准", "fine": "精细"}[x],
            index=0,
            key="input_quality",
        )
        st.session_state.quality = quality
    with col_count:
        script_count = st.number_input(
            "输出数目",
            min_value=1, max_value=MAX_SCRIPT_COUNT, value=1, step=1,
            key="input_count",
        )
        st.session_state.script_count = script_count
    clicked = st.button(
        "开始生成脚本", type="primary", use_container_width=True,
        disabled=not bool(video_url.strip()),
    )
    if clicked:
        _type = st.session_state.script_type_selection
        _quality = st.session_state.quality
        _count = min(st.session_state.script_count, MAX_SCRIPT_COUNT)
        clear_run()
        st.session_state.video_url = video_url.strip()
        st.session_state.script_type_selection = _type
        st.session_state.quality = _quality
        st.session_state.script_count = _count
        st.session_state.step = 1
        st.rerun()


# ============================================================
# 结果面板
# ============================================================
def render_result_panel():
    # 进入结果面板立即清理过期文档
    _cleanup_expired_docs()

    st.success("全部完成")

    if st.session_state.error:
        st.error(st.session_state.error)
        if st.button("清除错误，重新开始", use_container_width=True):
            clear_run()
            st.rerun()

    scripts = st.session_state.get("script_jsons", [])
    doc_urls = st.session_state.get("doc_urls", [])
    doc_url = st.session_state.get("doc_url", "")

    # ==== 文档链接列表 ====
    if doc_urls:
        for i, url in enumerate(doc_urls):
            script = scripts[i] if i < len(scripts) else {}
            title = script.get("title", f"脚本 {i+1}")
            label = f"脚本 {i+1}：{title}" if len(doc_urls) > 1 else f"脚本：{title}"
            st.markdown(f"""
            <div style="border: 1px solid #d0d5dd; border-radius: 8px; padding: 20px; margin: 12px 0;">
                <p style="font-size: 1em; font-weight: 600; margin: 0 0 8px 0; color: #333;">{label}</p>
                <a href="{url}" target="_blank" style="font-size: 0.9em; color: #1a56db;
                    word-break: break-all; text-decoration: none;">{url}</a>
            </div>
            """, unsafe_allow_html=True)
    elif doc_url:
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

    st.caption("💡 倒计时5分钟，退出界面后，文档自动删除")

    if st.button("生成新脚本", type="secondary", use_container_width=True):
        _expire_documents()
        clear_run()
        st.rerun()


# ============================================================
# 文档过期队列（文件持久化，不依赖线程）
# ============================================================

_EXPIRY_FILE = Path("data/.expiry_queue")  # JSONL: {"doc_id":..., "expires_at":...}


def _enqueue_expiry(doc_ids: list):
    """将文档 ID + 过期时间写入队列文件。"""
    if not doc_ids:
        return
    _EXPIRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    expiry = time.time() + DOC_TTL_SECONDS
    with open(_EXPIRY_FILE, "a") as f:
        for did in doc_ids:
            f.write(f'{{"doc_id":"{did}","expires_at":{expiry}}}\n')
    logger.info("已写入过期队列: %d 个文档", len(doc_ids))


def _cleanup_expired_docs():
    """清理所有已过期的飞书文档。"""
    if not _EXPIRY_FILE.exists():
        return
    try:
        client = _get_feishu_client()
    except Exception:
        return

    lines = _EXPIRY_FILE.read_text().splitlines()
    remaining = []
    deleted = 0
    now = time.time()
    for line in lines:
        if not line.strip():
            continue
        try:
            import json
            entry = json.loads(line)
            if now >= entry["expires_at"]:
                try:
                    ok = client.delete_document(entry["doc_id"])
                    if ok:
                        logger.info("过期文档已删除: %s", entry["doc_id"])
                        deleted += 1
                    else:
                        logger.warning("过期文档 %s 删除API返回失败", entry["doc_id"])
                        remaining.append(line)
                        continue
                except Exception as e:
                    logger.warning("删除过期文档 %s 异常: %s", entry["doc_id"], e)
                    remaining.append(line)
                    continue
            else:
                remaining.append(line)
        except Exception:
            remaining.append(line)

    if deleted:
        logger.info("本次清理过期文档: %d 个", deleted)
    if remaining:
        _EXPIRY_FILE.write_text("\n".join(remaining))
    else:
        _EXPIRY_FILE.unlink(missing_ok=True)


def _expire_documents(doc_ids: list = None):
    """删除飞书文档 + 清理 session。doc_ids 为空时从 session_state 读取。"""
    if doc_ids is None:
        doc_ids = st.session_state.get("created_doc_ids", [])
    if doc_ids:
        try:
            client = _get_feishu_client()
        except Exception:
            client = None
        for did in doc_ids:
            try:
                if client:
                    client.delete_document(did)
                logger.info("过期文档已删除: %s", did)
            except Exception as e:
                logger.warning("删除过期文档 %s 失败: %s", did, e)
    if doc_ids is None:
        st.session_state.created_doc_ids = []
    _cleanup_session()


# ============================================================
# 进度面板
# ============================================================

STEP_LABELS = {
    1: ("① 提取视频", "正在下载抖音视频并提取标题/作者..."),
    2: ("② AI 分析", "正在语音转文字、AI 综合分析..."),
    3: ("③ 生成脚本", "正在根据分析结果生成脚本..."),
    4: ("④ 审核微调", "AI 正在逐项审核并修正脚本..."),
    5: ("⑤ 飞书文档", "正在创建飞书文档并填充脚本内容..."),
}

# 不同质量等级的时间估算（精细模式使用更好的语音转文字模型，耗时更长）
_EST_STD  = {1: "约 5-10 秒", 2: "约 60-120 秒", 3: "约 10-25 秒", 4: "约 30-60 秒", 5: "约 5-12 秒"}
_EST_FINE = {1: "约 5-10 秒", 2: "约 90-180 秒", 3: "约 15-30 秒", 4: "约 30-60 秒", 5: "约 5-15 秒"}

def _get_step_estimates():
    q = st.session_state.get("quality", "standard")
    return {"standard": _EST_STD, "fine": _EST_FINE}.get(q, _EST_STD)

def render_progress_panel():
    step = st.session_state.step
    label, desc = STEP_LABELS.get(step, ("处理中...", ""))
    estimate = _get_step_estimates().get(step, "")

    # 进度条
    st.progress((step - 1) / 5, text=f"步骤 {step}/5")

    # 步骤标题
    st.markdown(f"<h3 style='text-align:center; margin:0.75rem 0 0.25rem 0;'>{label}</h3>", unsafe_allow_html=True)
    # spinner 行：左文字 ⏳ 右预计时间
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

    status_placeholder = st.empty()
    status_placeholder.info(st.session_state.status_msg or desc)

    # 取消按钮
    if st.button("⏹ 取消生成", key="cancel_btn", type="secondary", use_container_width=True):
        st.session_state.cancel_requested = True
        st.rerun()

    # 首次进入进度面板 → 显示「准备中」→ 下一次 rerun 再实际执行
    if not st.session_state.pipeline_started:
        st.session_state.pipeline_started = True
        st.session_state.status_msg = "正在准备，请稍候..."
        time.sleep(0.3)
        st.rerun()

    # 执行当前步骤
    try:
        if step == 1:
            step1_extract()
        elif step == 2:
            step2_analyze()
        elif step == 3:
            step3_generate()
        elif step == 4:
            step4_review()
        elif step == 5:
            step5_feishu()
    except StepCancelledError:
        _cleanup_session()
        st.session_state.step = 0
        st.session_state.error = "已取消生成"
        st.rerun()

    # 清理 spinner 占位
    spinner_placeholder.empty()

    # 步骤完成 → 推进
    if st.session_state.step != step:
        st.rerun()


# ============================================================
# 管道步骤
# ============================================================

def _session_dir() -> Path:
    """当前 session 的临时文件根目录。"""
    return Path("data") / st.session_state.session_id


def step1_extract():
    st.session_state.status_msg = "正在获取抖音视频..."
    try:
        _touch_heartbeat()
        _check_cancel()
        downloads = _session_dir() / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        extractor = DouyinExtractor()
        result = extractor.extract(st.session_state.video_url, str(downloads))
        st.session_state.video_path = result["video_path"]
        st.session_state.video_title = result["title"]
        st.session_state.video_author = result["author"]
        st.session_state.step = 2
        st.session_state.status_msg = f"已获取视频: {result['title'][:30]}"
        logger.info(f"视频提取成功: {result['title'][:30]}")
    except StepCancelledError:
        raise
    except DouyinError as e:
        _cleanup_session()
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"视频提取失败: {e}")
    except Exception as e:
        _cleanup_session()
        st.session_state.error = f"视频提取异常: {e}"
        st.session_state.step = 0
        logger.exception(f"视频提取异常: {e}")


def step2_analyze():
    st.session_state.status_msg = "AI 分析中..."
    try:
        _touch_heartbeat()
        _check_cancel()
        analyzer = VideoAnalyzer()
        sdir = str(_session_dir())
        result = analyzer.analyze(
            st.session_state.video_path,
            st.session_state.video_title,
            st.session_state.video_author,
            session_dir=sdir,
            quality=st.session_state.get("quality", "standard"),
        )
        _check_cancel()
        audio_text = result.get("audio_transcript", "")
        audio_note = "（含语音转文字）" if audio_text and "转录" in audio_text else ""
        st.session_state.synthesis = result["synthesis"]
        st.session_state.audio_transcript = audio_text

        # 自动检测脚本类型（或使用用户手动选择）
        selection = st.session_state.get("script_type_selection", "auto")
        if selection == "auto":
            gen = ScriptGenerator()
            detected = gen.detect_type(result["synthesis"], st.session_state.video_title, audio_text)
            st.session_state.script_type = detected
            logger.info("自动检测脚本类型: %s", detected)
        else:
            st.session_state.script_type = selection

        st.session_state.step = 3
        st.session_state.status_msg = f"分析完成 {audio_note}"
        logger.info("AI 分析完成")
    except StepCancelledError:
        raise
    except VideoAnalysisError as e:
        _cleanup_session()
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"视频分析失败: {e}")
    except Exception as e:
        _cleanup_session()
        st.session_state.error = f"视频分析异常: {e}"
        st.session_state.step = 0
        logger.exception(f"视频分析异常: {e}")


def step3_generate():
    synthesis = st.session_state.synthesis
    video_title = st.session_state.video_title
    script_count = st.session_state.get("script_count", 1)
    st.session_state.status_msg = f"正在生成{'脚本' if script_count == 1 else f'{script_count} 个脚本'}..."

    if not synthesis or not synthesis.strip():
        _cleanup_session()
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

        # 从音频转录估算原视频口播字数，用于约束仿写篇幅
        st.session_state.target_chars = 0
        target_chars = 0
        if audio_transcript:
            import re
            # 提取「音频转录:」之后的内容，去除元数据行
            marker = "音频转录:\n"
            idx = audio_transcript.find(marker)
            if idx >= 0:
                text = audio_transcript[idx + len(marker):]
                text = re.sub(r'\n语言:[^\n]*', '', text)
                text = re.sub(r'\n?⚠️.*', '', text, flags=re.DOTALL)
            else:
                text = audio_transcript
            # 统计中文字数（包含中文标点）
            chinese = len(re.findall(r'[一-鿿㐀-䶿豈-﫿]', text))
            # 统计英文/数字词数
            english_words = len(re.findall(r'[a-zA-Z0-9]+', text))
            target_chars = chinese + english_words
            st.session_state.target_chars = target_chars
            logger.info("原视频口播估算字数: %d（中文 %d + 英文/数字 %d）", target_chars, chinese, english_words)

        # 无音频时：用视频时长估算（说话速度 ~3字/秒），保底 200 字
        if target_chars == 0:
            duration = st.session_state.get("video_duration", 0)
            target_chars = max(100, int(duration * 3)) if duration > 0 else 200
            st.session_state.target_chars = target_chars
            logger.info("无音频转录，根据时长估算字数: %d", target_chars)

        _touch_heartbeat()
        _check_cancel()

        if script_count == 1:
            scripts = [gen.generate(synthesis, script_type=st.session_state.script_type,
                                    video_title=video_title, audio_transcript=audio_transcript,
                                    target_chars=target_chars)]
        else:
            scripts = gen.generate_multiple(synthesis, st.session_state.script_type,
                                            script_count, video_title=video_title,
                                            audio_transcript=audio_transcript,
                                            target_chars=target_chars)

        st.session_state.script_jsons = scripts
        st.session_state.script_json = scripts[0]
        st.session_state.step = 4
        st.session_state.status_msg = f"{'脚本已生成' if script_count == 1 else f'{len(scripts)} 个脚本已生成'}"
        logger.info("脚本生成成功 (%d 个)", len(scripts))
    except StepCancelledError:
        raise
    except ScriptGeneratorError as e:
        _cleanup_session()
        st.session_state.error = str(e)
        st.session_state.step = 0
        logger.error(f"脚本生成失败: {e}")
    except Exception as e:
        _cleanup_session()
        st.session_state.error = f"脚本生成异常: {e}"
        st.session_state.step = 0
        logger.exception(f"脚本生成异常: {e}")


def step4_review():
    """AI 审核微调：对照原始 Prompt 逐项校验，修正格式偏差和内容缺失。"""
    script = st.session_state.script_json
    script_type = st.session_state.script_type
    total = len(st.session_state.get("script_jsons", [script]))
    st.session_state.status_msg = "AI 正在审核主脚本..." if total > 1 else "AI 正在审核脚本..."

    try:
        _touch_heartbeat()
        _check_cancel()
        gen = ScriptGenerator()
        audio_transcript = st.session_state.get("audio_transcript", "")
        if script_type == "oral":
            original_prompt = build_oral_prompt(
                st.session_state.synthesis, audio_transcript=audio_transcript,
                target_chars=st.session_state.get("target_chars", 0))
        else:
            original_prompt = build_mix_prompt(
                st.session_state.synthesis, audio_transcript=audio_transcript,
                target_chars=st.session_state.get("target_chars", 0))

        refined, note = gen.review(
            script, script_type, original_prompt,
            synthesis=st.session_state.get("synthesis", ""),
            audio_transcript=st.session_state.get("audio_transcript", ""),
            target_chars=st.session_state.get("target_chars", 0))
        st.session_state.script_json = refined
        # 更新 script_jsons 中的主脚本
        scripts = st.session_state.get("script_jsons", [])
        if scripts:
            scripts[0] = refined
            st.session_state.script_jsons = scripts
        st.session_state.step = 5
        st.session_state.status_msg = f"审核完成：{note}"
        logger.info("审核微调: %s", note)
    except StepCancelledError:
        raise
    except Exception as e:
        logger.warning("审核微调异常: %s", e)
        st.session_state.step = 5
        st.session_state.status_msg = "审核跳过（异常），使用原始脚本"


def step5_feishu():
    scripts = st.session_state.get("script_jsons", [st.session_state.script_json])
    if not scripts:
        scripts = [st.session_state.script_json]
    total = len(scripts)
    st.session_state.status_msg = f"正在创建飞书文档（1/{total}）..." if total > 1 else "正在创建飞书文档..."
    try:
        _touch_heartbeat()
        client = _get_feishu_client()
        doc_urls = []
        created_ids = []
        failed_count = 0

        for i, script in enumerate(scripts):
            _check_cancel()
            if total > 1:
                st.session_state.status_msg = f"正在创建飞书文档（{i+1}/{total}）..."
            try:
                result = client.create_and_fill(
                    st.session_state.script_type, script,
                    st.session_state.video_url, st.session_state.video_title,
                    seq=i + 1,
                )
                doc_urls.append(result["url"])
                created_ids.append(result["doc_id"])
            except Exception as doc_err:
                failed_count += 1
                logger.warning("文档 %d/%d 创建失败: %s", i + 1, total, doc_err)
                continue

        st.session_state.doc_urls = doc_urls
        st.session_state.doc_url = doc_urls[0] if doc_urls else ""
        st.session_state.created_doc_ids = created_ids
        st.session_state.doc_created_at = time.time()
        st.session_state.step = 6
        st.session_state.status_msg = ""

        # 写入过期队列文件（main 入口每次检查，不依赖线程）
        _enqueue_expiry(created_ids)

        # 清理本地临时文件（session 目录）
        _cleanup_session()

        # 汇总信息
        if failed_count:
            status = f"完成：{len(doc_urls)}/{total} 个文档创建成功"
            if failed_count == total:
                st.session_state.error = "所有飞书文档创建均失败，请稍后重试。"
                st.session_state.step = 0
                return
            st.session_state.status_msg = f"{status}，{failed_count} 个失败"
        logger.info("飞书文档创建完成: %d/%d", len(doc_urls), total)
    except StepCancelledError:
        # 即使取消，已创建的文档也返回（让用户有时间转存）
        st.session_state.created_doc_ids = created_ids
        st.session_state.doc_created_at = time.time()
        _enqueue_expiry(list(created_ids))
        st.session_state.step = 6
        st.session_state.status_msg = "部分文档已创建（生成被取消）"
    except FeishuError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        _cleanup_session()
        logger.error(f"飞书操作失败: {e}")
    except Exception as e:
        st.session_state.error = f"飞书操作异常: {e}"
        st.session_state.step = 0
        _cleanup_session()
        logger.exception(f"飞书操作异常: {e}")


# ============================================================
# 主函数
# ============================================================
def main():
    # 启动清理：清除上次运行的残留数据（模块级全局变量确保整个进程只执行一次）
    _cleanup_stale_data()

    # 心跳：每次 rerun 刷新存活时间，供 _check_cancel() 检测浏览器断连
    sid = st.session_state.get("session_id", "")
    if sid:
        hb = Path(f"data/{sid}/.heartbeat")
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.touch()

    # 过期文档清理（每次请求都检查）
    _cleanup_expired_docs()

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
    elif step in (1, 2, 3, 4, 5):
        render_progress_panel()
    elif step == 6:
        render_result_panel()


if __name__ == "__main__":
    main()
