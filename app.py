"""
短视频脚本生成系统
输入抖音链接 → AI 分析 + 类型检测 → 生成脚本 → AI 审核 → 飞书文档（5 步）
"""
import json
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
from config import DOC_TTL_SECONDS  # noqa: E402


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


@st.cache_resource
def _get_image_matcher():
    from src.image_matcher import ImageMatcher
    client = _get_feishu_client()
    return ImageMatcher(feishu_client=client)


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
    "script_type_selection": "mix",
    "script_type": "mix",
    "quality": "standard",
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


def _cleanup_stale_data():
    """每次请求清理 data/ 下超过 10 分钟的旧 session 目录。

    通过检查 session 目录下的 .heartbeat 文件判断是否活跃：
    - .heartbeat 在每次 rerun 时由 main() 刷新
    - 若 .heartbeat 超过 10 分钟未更新 → session 已死
    - 无 .heartbeat 的旧目录，以目录 mtime 作为备用判断
    """
    data_dir = Path("data")
    if not data_dir.exists():
        return

    now = time.time()
    stale_age = 600  # 10 分钟
    current_sid = st.session_state.get("session_id", "")
    for item in data_dir.iterdir():
        if not item.is_dir():
            continue
        if item.name.startswith("."):
            continue
        if item.name == current_sid:
            continue
        try:
            hb = item / ".heartbeat"
            if hb.exists():
                age = now - hb.stat().st_mtime
            else:
                # 旧目录没有心跳文件，用目录 mtime 兜底
                age = now - item.stat().st_mtime
            if age > stale_age:
                shutil.rmtree(item, ignore_errors=True)
                logger.info("清理残留 session: %s", item)
        except Exception:
            pass


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
    # 类型 / 质量 同行
    col_type, col_quality = st.columns([1, 1])
    with col_type:
        script_type_selection = st.selectbox(
            "脚本类型",
            options=["mix", "oral"],
            format_func=lambda x: {"mix": "混剪", "oral": "口播"}[x],
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
    clicked = st.button(
        "开始生成脚本", type="primary", use_container_width=True,
        disabled=not bool(video_url.strip()),
    )
    if clicked:
        _type = st.session_state.script_type_selection
        _quality = st.session_state.quality
        clear_run()
        st.session_state.video_url = video_url.strip()
        st.session_state.script_type_selection = _type
        st.session_state.quality = _quality
        st.session_state.step = 1
        st.rerun()


# ============================================================
# 结果面板
# ============================================================
def render_result_panel():
    # 进入结果面板立即清理过期文档 + 本地临时文件（视频已上传飞书，无需保留）
    _cleanup_expired_docs()
    _cleanup_session()

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
    """将文档 ID + 过期时间写入队列文件（追加模式）。"""
    if not doc_ids:
        return
    _EXPIRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    expiry = time.time() + DOC_TTL_SECONDS
    new_lines = [f'{{"doc_id":"{did}","expires_at":{expiry}}}' for did in doc_ids]
    # 读取已有条目 → 追加 → 原子写入，避免与 _cleanup_expired_docs 竞态
    existing = _EXPIRY_FILE.read_text().strip() if _EXPIRY_FILE.exists() else ""
    all_lines = ([existing] if existing else []) + new_lines
    tmp = _EXPIRY_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(all_lines), encoding="utf-8")
    tmp.replace(_EXPIRY_FILE)
    logger.info("已写入过期队列: %d 个文档", len(doc_ids))


def _cleanup_expired_docs():
    """清理所有已过期的飞书文档。"""
    if not _EXPIRY_FILE.exists():
        return
    try:
        client = _get_feishu_client()
    except Exception:
        return

    raw = _EXPIRY_FILE.read_text()
    raw = raw.replace("}{", "}\n{")
    lines = raw.splitlines()
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
                        logger.warning("过期文档 %s 删除API返回失败，丢弃", entry["doc_id"])
                except Exception as e:
                    logger.warning("删除过期文档 %s 异常，丢弃: %s", entry["doc_id"], e)
            else:
                remaining.append(line)
        except Exception:
            pass

    if deleted:
        logger.info("本次清理过期文档: %d 个", deleted)
    if remaining:
        tmp = _EXPIRY_FILE.with_suffix(".tmp")
        tmp.write_text("\n".join(remaining), encoding="utf-8")
        tmp.replace(_EXPIRY_FILE)
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
    1: ("1 提取视频", "正在下载抖音视频并提取标题/作者..."),
    2: ("2 语音转文字", "正在提取音频并语音转文字..."),
    3: ("3 生成脚本", "正在根据分析结果生成脚本..."),
    4: ("4 审核微调", "AI 正在逐项审核并修正脚本..."),
    5: ("5 飞书文档", "正在创建飞书文档并填充文字..."),
    6: ("6 AI插图", "AI 正在匹配表情包并插入文档..."),
}

# 不同质量等级的时间估算（精细模式使用更好的语音转文字模型，耗时更长）
_EST_STD  = {1: "约 5-10 秒", 2: "约 60-120 秒", 3: "约 10-25 秒", 4: "约 30-60 秒", 5: "约 5-12 秒", 6: "约 10-25 秒"}
_EST_FINE = {1: "约 5-10 秒", 2: "约 90-180 秒", 3: "约 15-30 秒", 4: "约 30-60 秒", 5: "约 5-15 秒", 6: "约 12-30 秒"}

def _get_step_estimates():
    q = st.session_state.get("quality", "standard")
    return {"standard": _EST_STD, "fine": _EST_FINE}.get(q, _EST_STD)

def render_progress_panel():
    step = st.session_state.step
    label, desc = STEP_LABELS.get(step, ("处理中...", ""))
    estimate = _get_step_estimates().get(step, "")

    # 进度条
    st.progress((step - 1) / 6, text=f"步骤 {step}/6")

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
        elif step == 6:
            step6_images()
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
    st.session_state.status_msg = "语音转文字中..."
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
        audio_note = "（语音转文字）" if audio_text and "转录" in audio_text else ""
        st.session_state.synthesis = ""  # synthesis 已删除，留空保底
        st.session_state.audio_transcript = audio_text

        # 用户手动选择脚本类型
        st.session_state.script_type = st.session_state.get("script_type_selection", "mix")

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
    from config import FIXED_TARGET_CHARS_MIX, FIXED_TARGET_CHARS_ORAL

    video_title = st.session_state.video_title
    script_type = st.session_state.script_type
    st.session_state.status_msg = "正在生成脚本..."

    try:
        gen = ScriptGenerator()
        audio_transcript = st.session_state.get("audio_transcript", "")
        # Whisper 默认输出繁体 + 英文标点，转为简体中文 + 中文标点
        if audio_transcript:
            try:
                from zhconv import convert
                audio_transcript = convert(audio_transcript, "zh-cn")
            except ImportError:
                pass
            # 标点符号规范化：Whisper 默认输出英文标点，替换为中文标点
            # 只改标点编码，不改任何文字内容
            _punct_map = {',': '，', '.': '。', '!': '！', '?': '？', ':': '：', ';': '；'}
            for _en, _zh in _punct_map.items():
                audio_transcript = audio_transcript.replace(_en, _zh)

        # 按脚本类型使用不同的目标字数（下限, 上限）
        target_lo, target_chars = FIXED_TARGET_CHARS_ORAL if script_type == "oral" else FIXED_TARGET_CHARS_MIX
        st.session_state.target_chars = target_chars
        logger.info("目标字数: %d~%d", target_lo, target_chars)

        _touch_heartbeat()
        _check_cancel()

        script = gen.generate(script_type=script_type,
                              video_title=video_title, audio_transcript=audio_transcript,
                              target_lo=target_lo, target_chars=target_chars)

        # 口播脚本：程序化兜底 original_text 英文标点 → 中文标点
        # 确保即使 prompt 指令未完全生效，原片文案的标点也是中文的
        if script_type == "oral" and "original_text" in script:
            _punct_map_oral = {',': '，', '.': '。', '!': '！', '?': '？', ':': '：', ';': '；'}
            for _en, _zh in _punct_map_oral.items():
                script["original_text"] = script["original_text"].replace(_en, _zh)

        st.session_state.script_jsons = [script]
        st.session_state.script_json = script
        st.session_state.target_lo = target_lo
        st.session_state.rollback_count = 0
        st.session_state.step = 4  # 进入审核
        st.session_state.status_msg = "脚本已生成，正在审核..."
        logger.info("脚本生成成功")
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
    """全维度程序化合规检查 + 回退/微调分流。

    串行逻辑：
    阶段 1：回退循环（格式/长度）— 最多 1 次
    阶段 2：AI 微调 — 合并修复格式/长度/相似度/AI 味，再独立处理标记分布
    """
    script = st.session_state.script_json
    script_type = st.session_state.script_type
    audio_transcript = st.session_state.get("audio_transcript", "")
    target_lo = st.session_state.get("target_lo", 0)
    target_chars = st.session_state.get("target_chars", 0)
    video_title = st.session_state.get("video_title", "")
    MAX_ROLLBACK = 1

    st.session_state.status_msg = "正在审核脚本..."

    try:
        gen = ScriptGenerator()

        # ===== 阶段 1：回退循环（格式 + 长度） =====
        for attempt in range(MAX_ROLLBACK + 1):
            _touch_heartbeat()
            _check_cancel()

            if script_type == "mix":
                script = gen._normalize_mix_punctuation(script)

            report = gen.review(
                script, script_type,
                audio_transcript=audio_transcript,
                target_lo=target_lo, target_chars=target_chars)
            st.session_state.review_report = report

            if not report["needs_rollback"]:
                logger.info("阶段1 审核通过（回退 %d 次后达标）", attempt)
                break

            if attempt < MAX_ROLLBACK:
                rollback_count = st.session_state.rollback_count + 1
                st.session_state.rollback_count = rollback_count
                logger.info("回退重生成 %d/%d: format=%s length=%s",
                            rollback_count, MAX_ROLLBACK,
                            report["format"]["pass"], report["length"]["pass"])
                st.session_state.status_msg = f"审核不达标，回退重生成（{rollback_count}/{MAX_ROLLBACK}）..."
                script = gen.generate(
                    script_type=script_type,
                    video_title=video_title,
                    audio_transcript=audio_transcript,
                    target_lo=target_lo, target_chars=target_chars)
            else:
                logger.warning("回退耗尽（%d 次），交由微调修复", MAX_ROLLBACK)
                break

        # ===== 阶段 2：AI 微调（串行：正文 → 标记） =====
        needs_any_fix = report["needs_rollback"] or report["needs_micro"]
        if needs_any_fix:
            st.session_state.status_msg = "正在 AI 微调..."

            # 正文微调：格式 + 长度 + 相似度 + AI 味（一次 AI 调用）
            script = gen.micro_adjust(
                script, script_type, report,
                audio_transcript=audio_transcript,
                target_lo=target_lo, target_chars=target_chars)

            # 标记微调：口播且标记分布单一（一次小型 AI 调用）
            if script_type == "oral" and not report["marker_distribution"]["pass"]:
                script = gen.micro_adjust_markers(script)

            # 微调后重审
            if script_type == "mix":
                script = gen._normalize_mix_punctuation(script)
            report = gen.review(
                script, script_type,
                audio_transcript=audio_transcript,
                target_lo=target_lo, target_chars=target_chars)
            st.session_state.review_report = report
            st.session_state.status_msg = "微调完成"
            logger.info("微调后重审: rollback=%s micro=%s",
                        report["needs_rollback"], report["needs_micro"])
        else:
            st.session_state.status_msg = "审核通过 ✓"
            logger.info("审核全部通过")

        st.session_state.script_json = script
        _update_main_script(script)
        st.session_state.step = 5

    except StepCancelledError:
        raise
    except Exception as e:
        import traceback
        logger.warning("审核异常: %s\n%s", e, traceback.format_exc())
        st.session_state.step = 5
        st.session_state.status_msg = "审核跳过（异常），使用原始脚本"


def _update_main_script(script: dict):
    """更新 script_jsons 中的主脚本（索引 0）。"""
    scripts = st.session_state.get("script_jsons", [])
    if scripts:
        scripts[0] = script
        st.session_state.script_jsons = scripts


def step5_feishu():
    script = st.session_state.script_json
    st.session_state.status_msg = "正在创建飞书文档..."
    try:
        _touch_heartbeat()
        client = _get_feishu_client()

        result = client.create_and_fill(
            st.session_state.script_type, script,
            st.session_state.video_url, st.session_state.video_title,
            seq=1,
        )

        st.session_state.doc_url = result["url"]
        st.session_state.doc_urls = [result["url"]]
        st.session_state.created_doc_ids = [result["doc_id"]]
        st.session_state.doc_created_at = time.time()
        st.session_state.step = 6  # 进入 AI 插图步骤
        st.session_state.status_msg = ""

        # 写入过期队列文件（main 入口每次检查，不依赖线程）
        _enqueue_expiry([result["doc_id"]])

        # 清理本地临时文件（session 目录）
        _cleanup_session()

        logger.info("飞书文档创建完成")
    except StepCancelledError:
        st.session_state.step = 7  # 跳过插图，直接到结果页
        st.session_state.status_msg = "文档创建被取消"
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


def step6_images():
    """在已生成的飞书文档中匹配并插入表情包图片。"""
    st.session_state.status_msg = "正在扫描表情包库并匹配..."

    try:
        _touch_heartbeat()
        _check_cancel()

        client = _get_feishu_client()
        matcher = _get_image_matcher()

        script = st.session_state.script_json
        script_type = st.session_state.script_type
        doc_id = st.session_state.created_doc_ids[0] if st.session_state.created_doc_ids else None

        if not doc_id:
            logger.warning("无 doc_id，跳过图片插入")
            st.session_state.step = 7
            return

        result = client.insert_all_images(doc_id, script, script_type, matcher)

        total, success, failed = result["total"], result["success"], result["failed"]
        if total > 0:
            st.session_state.status_msg = f"已插入 {success}/{total} 张图片" + (
                f"，{failed} 张失败" if failed else "")
        else:
            st.session_state.status_msg = "无图片素材，已跳过"

        st.session_state.step = 7
        logger.info("AI 插图完成: %d/%d 成功", success, total)

    except StepCancelledError:
        st.session_state.step = 7
        st.session_state.status_msg = "图片插入被取消"
    except Exception as e:
        logger.warning("AI 插图异常，跳过: %s", e)
        st.session_state.step = 7
        st.session_state.status_msg = f"图片插入异常（已跳过）: {e}"


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
    elif step in (1, 2, 3, 4, 5, 6):
        render_progress_panel()
    elif step == 7:
        render_result_panel()


if __name__ == "__main__":
    main()
