"""
短视频自动化脚本生成系统 - Streamlit Web App

输入抖音视频链接 - AI 分析视频 - 生成脚本 - 飞书文档 - 返回链接
"""
import sys
import time
import json
import re
import uuid
import logging
from pathlib import Path

import streamlit as st
from openai import OpenAI

from config import (
    QUALITY_PRESETS, get_quality_config, generate_doc_title,
    AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL, ADMIN_PASSWORD,
    save_admin_credentials,
)
from src.douyin_extractor import DouyinExtractor, DouyinError
from src.video_analyzer import VideoAnalyzer, VideoAnalysisError
from src.script_generator import ScriptGenerator, ScriptGeneratorError
from src.feishu_ops import FeishuClient, FeishuError
from src.session_manager import (
    save_checkpoint, load_checkpoint, delete_checkpoint,
    cleanup_old_sessions, get_session_dir,
)

# ============================================================
# 页面配置
# ============================================================

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
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app")


# ============================================================
# 缓存资源（避免每次 rerun 都重新创建）
# ============================================================

@st.cache_resource
def _get_ai_client():
    """缓存的 AI 客户端（跨 rerun 复用）"""
    return OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY)


@st.cache_resource
def _get_feishu_client():
    """缓存的飞书客户端（跨 rerun 复用）"""
    return FeishuClient()


# ============================================================
# Session State
# ============================================================

DEFAULTS = {
    "step": 0,
    "script_type": "auto",
    "quality": "standard",
    "video_url": "",
    "video_title": "",
    "video_author": "",
    "video_path": "",
    "synthesis": "",
    "script_json": None,
    "script_jsons": [],
    "script_count": 1,
    "custom_requirements": "",
    "req_images_extracted": "",
    "doc_url": "",
    "doc_urls": [],
    "doc_id": "",
    "doc_ids": [],
    "doc_created_at": 0.0,
    "error": None,
    "status_msg": "",
    "generation_complete": False,
    "elapsed_start": 0.0,
    "admin_mode": False,
    "admin_msgs": [],
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


def clear_run():
    saved_custom = st.session_state.get("custom_requirements", "")
    for key in DEFAULTS:
        st.session_state[key] = DEFAULTS[key]
    st.session_state.custom_requirements = saved_custom


def get_downloads_dir() -> Path:
    video_url = st.session_state.get("video_url", "")
    if video_url:
        return get_session_dir(video_url)
    if "download_dir_id" not in st.session_state:
        st.session_state.download_dir_id = uuid.uuid4().hex[:8]
    downloads = Path("data") / "downloads" / st.session_state.download_dir_id
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


# ============================================================
# 文档生命周期（5 分钟 TTL）
# ============================================================

_doc_registry_path = Path("data") / "doc_registry.json"
_DOC_TTL = 300  # 5 分钟


def _load_doc_registry() -> dict:
    if not _doc_registry_path.exists():
        return {}
    try:
        return json.loads(_doc_registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_doc_registry(registry: dict):
    _doc_registry_path.parent.mkdir(parents=True, exist_ok=True)
    _doc_registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def register_docs(doc_ids: list, doc_urls: list):
    registry = _load_doc_registry()
    now = time.time()
    for doc_id, doc_url in zip(doc_ids, doc_urls):
        registry[doc_id] = {"url": doc_url, "created_at": now}
    _save_doc_registry(registry)


def delete_expired_docs():
    """删除过期文档。网络错误静默忽略，不阻塞启动。"""
    registry = _load_doc_registry()
    if not registry:
        return
    now = time.time()
    expired = [did for did, m in registry.items() if now - m["created_at"] > _DOC_TTL]
    if not expired:
        return
    try:
        client = _get_feishu_client()
    except Exception:
        return
    deleted = 0
    for doc_id in expired:
        try:
            if client.delete_document(doc_id):
                del registry[doc_id]
                deleted += 1
        except Exception:
            pass  # 单个文档删除失败不影响其他
    if deleted:
        _save_doc_registry(registry)
        logger.info(f"已清理 {deleted} 个过期文档")


# ============================================================
# 面板一：输入
# ============================================================

def render_input_panel():
    col_url, col_type, col_quality, col_count = st.columns([3, 1, 1, 0.7])

    with col_url:
        video_url = st.text_input(
            "抖音视频链接",
            placeholder="在此处直接粘贴视频链接",
            key="input_url",
            label_visibility="visible",
        )

    with col_type:
        script_type = st.selectbox(
            "脚本类型",
            options=["auto", "mix", "oral"],
            format_func=lambda x: {"auto": "自动检测", "mix": "混剪", "oral": "口播"}[x],
            key="input_type",
        )

    with col_quality:
        quality = st.selectbox(
            "输出质量",
            options=["fast", "standard", "fine"],
            format_func=lambda x: {"fast": "快速", "standard": "标准", "fine": "精细"}[x],
            key="input_quality",
        )

    with col_count:
        script_count = st.number_input(
            "数量",
            min_value=1, max_value=5, value=1, step=1,
            key="input_count",
        )

    with st.expander("自定义要求（可选）", expanded=False):
        st.markdown('<p style="font-size:0.875rem; margin:0 0 0.25rem 0;">文字描述</p>', unsafe_allow_html=True)
        custom = st.text_area(
            "",
            value=st.session_state.get("custom_requirements", ""),
            height=80,
            placeholder="例：标题短一点，只要 8 行，不要广告，语气轻松活泼",
            key="custom_req_input",
            label_visibility="collapsed",
        )
        st.markdown('<p style="font-size:0.875rem; margin:0 0 0.25rem 0;">上传图片</p>', unsafe_allow_html=True)
        uploaded_files = st.file_uploader(
            "",
            type=["png", "jpg", "webp"],
            accept_multiple_files=True,
            key="req_image_uploader",
            label_visibility="collapsed",
        )
        extracted = st.session_state.get("req_images_extracted", "")
        if extracted:
            st.caption(f"已从图片识别：{extracted[:80]}{'...' if len(extracted)>80 else ''}")

    can_generate = bool(video_url.strip())
    clicked = st.button("开始生成脚本", type="primary", use_container_width=True,
                        disabled=not can_generate)

    if clicked:
        clear_run()
        final_text = custom.strip() if custom else ""

        if uploaded_files:
            with st.spinner("正在识别上传图片中的要求..."):
                gen = ScriptGenerator()
                extracted_texts = []
                for uf in uploaded_files:
                    try:
                        image_bytes = uf.read()
                        result = gen.extract_requirements_from_image(image_bytes, uf.name)
                        if result:
                            extracted_texts.append(result)
                    except Exception as e:
                        st.warning(f"图片 {uf.name} 识别失败: {e}")
                if extracted_texts:
                    combined = "\n".join(extracted_texts)
                    st.session_state.req_images_extracted = combined
                    final_text = (final_text + "\n\n" + combined) if final_text else combined

        st.session_state.custom_requirements = final_text
        st.session_state.video_url = video_url.strip()
        st.session_state.script_type = script_type
        st.session_state.quality = quality
        st.session_state.script_count = script_count
        st.session_state.elapsed_start = time.time()
        st.session_state.step = 1
        st.rerun()


# ============================================================
# 面板二：进度
# ============================================================

# 各步骤预计时间（按输出质量分级，确保单步 ≤ 总预计）
_STEP_TIMES = {
    "fast":      {1: "约 10-20 秒", 2: "约 30-90 秒", 3: "约 10-20 秒", 4: "约 5-10 秒"},
    "standard":  {1: "约 15-30 秒", 2: "约 2-4 分钟", 3: "约 10-25 秒", 4: "约 5-12 秒"},
    "fine":      {1: "约 20-40 秒", 2: "约 5-9 分钟", 3: "约 10-30 秒", 4: "约 5-15 秒"},
}


def render_progress_panel():
    step = st.session_state.step
    step_labels = {1: "提取视频", 2: "AI 分析视频", 3: "生成脚本", 4: "创建飞书文档"}

    quality = st.session_state.get("quality", "standard")
    step_times = _STEP_TIMES.get(quality, _STEP_TIMES["standard"])

    elapsed = time.time() - st.session_state.elapsed_start
    e_str = f"{int(elapsed // 60)} 分 {int(elapsed % 60)} 秒" if elapsed >= 60 else f"{int(elapsed)} 秒"

    preset = get_quality_config(st.session_state.quality)
    msg = st.session_state.get("status_msg", "")

    filled = "●" * (step - 1)
    current = "◉"
    empty = "○" * (4 - step)
    bar = "  ".join([filled, current, empty]) if filled else "  ".join([current, empty])

    st.markdown(f"""
    <div style="text-align:center; margin: 1rem 0 1.5rem 0;">
        <div style="font-size: 1.5em; letter-spacing: 4px; margin-bottom: 0.75rem;">{bar}</div>
        <div style="font-size: 1.05em; color: #333; margin: 0.5rem 0;">
            第 {step} 步 &middot; {step_labels.get(step, '...')}
        </div>
        <div style="color: #999; font-size: 0.85em;">
            已用时 {e_str} &nbsp; | &nbsp; 本步预计 {step_times.get(step, '')} &nbsp; | &nbsp; 总预计 {preset['est_time']}
        </div>
    </div>
    """, unsafe_allow_html=True)

    if msg:
        st.info(msg)


# ============================================================
# 面板三：结果
# ============================================================

def render_result_panel():
    st.success("全部完成")

    doc_urls = st.session_state.get("doc_urls", [])
    script_jsons = st.session_state.get("script_jsons", [])

    if not doc_urls:
        doc_urls = [st.session_state.doc_url] if st.session_state.doc_url else []
    if not script_jsons:
        script_jsons = [st.session_state.script_json] if st.session_state.script_json else []

    # 过滤掉空值
    doc_urls = [u for u in doc_urls if u]
    script_jsons = [s for s in script_jsons if s]

    if st.session_state.get("doc_ids"):
        st.warning("文档将在 5 分钟后自动删除，请尽快保存副本")

    n = max(len(script_jsons), len(doc_urls))
    if n == 0:
        st.error("未生成任何结果。请返回重试。")
        if st.button("重新开始", use_container_width=True):
            clear_run()
            st.rerun()
        return

    st.markdown(f"### 已生成 {n} 个脚本")

    for i in range(n):
        doc_url = doc_urls[i] if i < len(doc_urls) else ""
        script = script_jsons[i] if i < len(script_jsons) else {}

        title = script.get("title", f"脚本{i+1}") if script else f"脚本{i+1}"
        label = f"脚本 {i+1}"

        st.markdown(f"""
        <div style="border: 1px solid #d0d5dd; border-radius: 8px; padding: 20px; margin: 12px 0;">
            <p style="font-size: 1em; font-weight: 600; margin: 0 0 8px 0; color: #333;">{label}：{title}</p>
            <a href="{doc_url}" target="_blank" style="font-size: 0.9em; color: #1a56db;
                word-break: break-all; text-decoration: none;">{doc_url if doc_url else '（无链接）'}</a>
        </div>
        """, unsafe_allow_html=True)

    st.divider()
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
# 管理后台（嵌入在同一个 App 中，密码保护）
# ============================================================

_ADMIN_REQ_PATH = Path(__file__).parent / "config" / "requirements.json"

_ADMIN_SYSTEM_PROMPT = """你是一个短视频脚本生成系统的配置助手。用户用自然语言描述想要修改的规则，你负责将他的意图精确翻译为 JSON 配置的变更。

当前系统有这些配置节：
- **模板配置**：飞书文件夹Token、混剪模板ID、口播模板ID、产品介绍库链接（统一管理外部链接）
- **通用**：语言、返回格式
- **混剪**：标题字数、行数范围、文案风格、素材格式/风格、广告植入
- **口播**：标题字数、对话轮数、角色格式、情绪选项、图片素材、对话结构
- **交付要求**：话题词数量/格式、【标题】【正文】发布信息

你的回复必须只包含一个 JSON：
```json
{
  "reply": "用通俗中文说明做了什么修改",
  "config": { ... 修改后的完整配置 JSON ... }
}
```

规则：
1. 只修改用户明确提到的字段，其他保持原样
2. 保留 _说明 字段不要改
3. 数字就是数字，字符串就是字符串，类型要对
4. 如果用户请求不明确，reply 里说明需要澄清"""


def _admin_config_to_text(data: dict) -> str:
    lines = []
    for section, items in data.items():
        if section.startswith("_"):
            continue
        lines.append(f"【{section}】")
        if isinstance(items, dict):
            for k, v in items.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {items}")
    return "\n".join(lines)


def _admin_ask_ai(user_msg: str, current_config: dict) -> dict:
    client = OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY)
    config_json = json.dumps(current_config, ensure_ascii=False, indent=2)
    config_readable = _admin_config_to_text(current_config)

    resp = client.chat.completions.create(
        model=AGNES_MODEL,
        messages=[
            {"role": "system", "content": _ADMIN_SYSTEM_PROMPT},
            {"role": "user", "content": f"当前配置：\n{config_readable}\n\nJSON：\n{config_json}"},
            {"role": "assistant", "content": "已就绪。"},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=2000,
        temperature=0.2,
    )

    raw = resp.choices[0].message.content.strip()
    md = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    json_str = md.group(1) if md else raw
    start = json_str.find("{")
    end = json_str.rfind("}")
    if start >= 0 and end > start:
        json_str = json_str[start:end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {"reply": f"无法解析 AI 返回，请重试。", "config": None}


def _admin_ask_ai_with_image(user_msg: str, current_config: dict, image_bytes: bytes, filename: str) -> dict:
    """带图片的管理员 AI 对话（先用 vision 识别图片内容，再结合文字请求）"""
    import base64 as _b64

    client = OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY)

    # Step 1: 从图片中提取要求文字
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    b64 = _b64.b64encode(image_bytes).decode("utf-8")
    b64_uri = f"data:image/{ext};base64,{b64}"

    try:
        vision_resp = client.chat.completions.create(
            model=AGNES_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": b64_uri}},
                    {"type": "text", "text": "请从这张图片中提取所有的配置修改要求和脚本规则要求。用中文大白话总结输出。如果图片中没有明确要求，回复'无'。"},
                ],
            }],
            max_tokens=800,
            temperature=0.2,
            timeout=30,
        )
        extracted = vision_resp.choices[0].message.content.strip()
    except Exception as e:
        extracted = f"[图片识别失败: {e}]"

    if extracted == "无" or not extracted:
        combined_msg = user_msg
    else:
        combined_msg = f"[从图片识别的要求: {extracted}]\n\n用户消息: {user_msg}" if user_msg else f"[从图片识别的要求: {extracted}]"

    # Step 2: 用合并后的文字请求配置助手
    return _admin_ask_ai(combined_msg, current_config)


def render_admin_panel():
    # ========== 中部：输入区 ==========
    st.markdown('<p style="font-size:0.875rem; margin:0 0 0.25rem 0;">文字描述</p>', unsafe_allow_html=True)
    prompt = st.chat_input("例如：混剪行数改成 8-12 行...")
    st.markdown('<p style="font-size:0.875rem; margin:0 0 0.25rem 0;">上传图片</p>', unsafe_allow_html=True)
    admin_upload = st.file_uploader(
        "",
        type=["png", "jpg", "jpeg", "webp"],
        key="admin_image_uploader",
        label_visibility="collapsed",
    )

    if prompt:
        user_msg = prompt
        display_msg = user_msg
        if admin_upload:
            display_msg = f"[{admin_upload.name}] {user_msg}"
        if admin_upload:
            user_msg = f"[上传图片: {admin_upload.name}] " + user_msg

        st.session_state.admin_msgs.append({"role": "user", "content": display_msg})
        with st.spinner("AI 正在理解你的要求..."):
            try:
                current = json.loads(_ADMIN_REQ_PATH.read_text(encoding="utf-8"))
            except Exception:
                current = {}

            if admin_upload:
                # 先用 vision 识别图片内容
                image_bytes = admin_upload.read()
                result = _admin_ask_ai_with_image(prompt, current, image_bytes, admin_upload.name)
            else:
                result = _admin_ask_ai(prompt, current)

        reply = result.get("reply", "")
        new_cfg = result.get("config")
        if new_cfg:
            st.session_state.admin_msgs.append({
                "role": "assistant",
                "content": reply,
                "pending_config": new_cfg,
                "resolved": False,
            })
        else:
            st.session_state.admin_msgs.append({"role": "assistant", "content": reply})
        st.rerun()

    st.divider()

    # ========== 底部：AI 对话历史 ==========
    for i, msg in enumerate(st.session_state.admin_msgs):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("pending_config") and not msg.get("resolved"):
                cfg = msg["pending_config"]
                with st.expander("查看变更", expanded=True):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.caption("当前")
                        try:
                            current = json.loads(_ADMIN_REQ_PATH.read_text(encoding="utf-8"))
                            st.code(_admin_config_to_text(current), language=None)
                        except Exception:
                            st.write("读取失败")
                    with c2:
                        st.caption("修改后")
                        st.code(_admin_config_to_text(cfg), language=None)
                a1, a2 = st.columns([1, 1])
                with a1:
                    if st.button("确认保存", key=f"adm_save_{i}", type="primary"):
                        with open(_ADMIN_REQ_PATH, "w", encoding="utf-8") as f:
                            json.dump(cfg, f, ensure_ascii=False, indent=2)
                        msg["resolved"] = True
                        msg["content"] += "\n\n已保存，即时生效。"
                        st.rerun()
                with a2:
                    if st.button("撤销", key=f"adm_undo_{i}"):
                        msg["resolved"] = True
                        msg["content"] += "\n\n已撤销。"
                        st.rerun()


# ============================================================
# 管道步骤（不创建 UI widget — 避免 ghost 元素）
# ============================================================

def step1_extract():
    st.session_state.status_msg = "正在获取抖音视频..."
    try:
        extractor = DouyinExtractor()
        downloads = get_downloads_dir()
        result = extractor.extract(st.session_state.video_url, str(downloads))
        st.session_state.video_path = result["video_path"]
        st.session_state.video_title = result["title"]
        st.session_state.video_author = result["author"]
        st.session_state.step = 2
        st.session_state.status_msg = f"已获取视频: {result['title'][:30]}"
        logger.info(f"视频提取成功: {result['title'][:30]}")
        save_checkpoint(st.session_state.video_url, _collect_checkpoint_state(2))
    except DouyinError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error(f"视频提取失败: {e}")


def step2_analyze():
    preset = get_quality_config(st.session_state.quality)
    st.session_state.status_msg = f"AI 分析中（{preset['label']}模式）..."
    try:
        analyzer = VideoAnalyzer()
        result = analyzer.analyze(
            st.session_state.video_path,
            st.session_state.video_title,
            st.session_state.video_author,
            quality=st.session_state.quality,
        )
        frame_count = len(result.get("frame_analysis", []))
        audio_text = result.get("audio_transcript", "")
        audio_note = "（含语音转文字）" if audio_text and "转录文字" in audio_text else ""
        st.session_state.synthesis = result["synthesis"]
        st.session_state.step = 3
        st.session_state.status_msg = f"分析完成: {frame_count} 帧 {audio_note}"
        logger.info(f"AI 分析完成: {frame_count} 帧")
        save_checkpoint(st.session_state.video_url, _collect_checkpoint_state(3))
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
    synthesis = st.session_state.synthesis
    video_title = st.session_state.video_title
    script_type = st.session_state.script_type
    script_count = st.session_state.get("script_count", 1)
    st.session_state.status_msg = "正在生成脚本..."

    # 边界检查：synthesis 为空时不应到达此步
    if not synthesis or not synthesis.strip():
        st.session_state.error = "视频分析结果为空，无法生成脚本。请重试。"
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error("synthesis 为空，跳过脚本生成")
        return

    try:
        gen = ScriptGenerator()
        if script_type == "auto":
            detected = gen.detect_type(synthesis, video_title)
            script_type = detected
            st.session_state.script_type = detected
            logger.info(f"检测结果: {'混剪' if detected == 'mix' else '口播'}")

        custom_req = st.session_state.get("custom_requirements", "")
        if script_count == 1:
            scripts = [gen.generate(synthesis, video_title, script_type, custom_req)]
        else:
            st.session_state.status_msg = f"正在生成 {script_count} 个脚本..."
            scripts = gen.generate_multiple(synthesis, video_title, script_type, script_count, custom_req)

        st.session_state.script_jsons = scripts
        st.session_state.script_json = scripts[0]
        st.session_state.step = 4
        type_name = "混剪" if script_type == "mix" else "口播"
        count_note = f"{len(scripts)} 个" if len(scripts) > 1 else "1 个"
        st.session_state.status_msg = f"{type_name}脚本已生成（{count_note}）"
        logger.info(f"脚本生成成功: {len(scripts)} 个")
        save_checkpoint(st.session_state.video_url, _collect_checkpoint_state(4))
    except ScriptGeneratorError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error(f"脚本生成失败: {e}")


def step4_feishu():
    scripts = st.session_state.get("script_jsons", [st.session_state.script_json])
    if not scripts:
        scripts = [st.session_state.script_json]
    total = len(scripts)

    try:
        client = _get_feishu_client()
        doc_urls, doc_ids = [], []
        failed = 0
        for i, script in enumerate(scripts):
            if script is None:
                failed += 1
                continue
            seq = i + 1
            if total > 1:
                st.session_state.status_msg = f"正在创建飞书文档（{seq}/{total}）..."
            else:
                st.session_state.status_msg = "正在创建飞书文档..."

            try:
                result = client.create_and_fill(
                    st.session_state.script_type, script,
                    st.session_state.video_url, st.session_state.video_title,
                    seq=seq,
                )
                doc_urls.append(result["url"])
                doc_ids.append(result["doc_id"])
            except FeishuError as e:
                logger.error(f"第 {seq} 个脚本飞书文档创建失败: {e}")
                failed += 1
                # 继续创建剩余文档

        if not doc_urls:
            raise FeishuError(f"全部 {total} 个文档创建均失败，请检查飞书配置。")

        st.session_state.doc_urls = doc_urls
        st.session_state.doc_url = doc_urls[0]
        st.session_state.doc_id = doc_ids[0]
        st.session_state.doc_ids = doc_ids
        st.session_state.doc_created_at = time.time()
        st.session_state.step = 5
        st.session_state.generation_complete = True
        if failed > 0:
            st.session_state.status_msg = f"{len(doc_urls)} 个成功，{failed} 个失败"
        else:
            st.session_state.status_msg = ""

        # 登记文档（5 分钟后自动删除）
        register_docs(doc_ids, doc_urls)
        # 清理旧 checkpoint
        delete_checkpoint(st.session_state.video_url)
        logger.info(f"飞书文档已创建: {len(doc_urls)} 个（{failed} 个失败）")
    except FeishuError as e:
        st.session_state.error = str(e)
        st.session_state.step = 0
        st.session_state.status_msg = ""
        logger.error(f"飞书操作失败: {e}")


# ============================================================
# Checkpoint & Recovery
# ============================================================

def _collect_checkpoint_state(step: int) -> dict:
    return {
        "step": step,
        "video_url": st.session_state.get("video_url", ""),
        "video_path": st.session_state.get("video_path", ""),
        "video_title": st.session_state.get("video_title", ""),
        "video_author": st.session_state.get("video_author", ""),
        "synthesis": st.session_state.get("synthesis", ""),
        "script_type": st.session_state.get("script_type", "auto"),
        "script_jsons": st.session_state.get("script_jsons", []),
        "script_json": st.session_state.get("script_json"),
        "quality": st.session_state.get("quality", "standard"),
        "custom_requirements": st.session_state.get("custom_requirements", ""),
        "script_count": st.session_state.get("script_count", 1),
        "doc_urls": st.session_state.get("doc_urls", []),
        "doc_ids": st.session_state.get("doc_ids", []),
    }


def check_recovery():
    if st.session_state.step != 0:
        return
    video_url = st.session_state.get("video_url", "")
    if not video_url:
        return
    saved = load_checkpoint(video_url)
    if not saved:
        return
    saved_step = saved.get("step", 0)
    if saved_step <= 1:
        return

    st.info(
        f"检测到未完成的生成任务 — 视频「{saved.get('video_title', '')[:30]}...」"
        f"在第 {saved_step - 1}/4 步中断"
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("恢复继续", use_container_width=True, type="primary"):
            for key in [
                "video_url", "video_path", "video_title", "video_author",
                "synthesis", "script_type", "quality", "custom_requirements",
                "script_count",
            ]:
                if key in saved:
                    st.session_state[key] = saved[key]
            if saved.get("script_jsons"):
                st.session_state.script_jsons = saved["script_jsons"]
                st.session_state.script_json = saved["script_jsons"][0]
            if saved.get("doc_urls"):
                st.session_state.doc_urls = saved["doc_urls"]
                st.session_state.doc_url = saved["doc_urls"][0]
            st.session_state.step = saved_step
            st.session_state.elapsed_start = time.time()
            st.rerun()
    with col2:
        if st.button("重新开始", use_container_width=True):
            delete_checkpoint(video_url)
            st.session_state.video_url = ""
            st.rerun()


# ============================================================
# 主函数 - 三步替换面板，st.empty() 防 ghost
# ============================================================

def main():
    # 隐藏 Streamlit 默认工具栏 + 页脚 + 悬浮锚点，保持全中文界面
    st.markdown("""
    <style>
    /* 强制滚动条始终可见，防止展开/收起 expander 时页面左右偏移 */
    html, body, [data-testid="stAppViewContainer"] { overflow-y: scroll !important; }
    html { scrollbar-gutter: stable; }
    /* 隐藏右上角 Streamlit 工具栏（Deploy / ⋮ 英文菜单） */
    [data-testid="stToolbar"] { display: none !important; }
    /* 隐藏页脚 "Made with Streamlit" */
    footer { display: none !important; }
    /* 隐藏标题悬浮锚点图标 */
    .stMarkdown h1 a, .stMarkdown h2 a, .stMarkdown h3 a { display: none; }
    /* 隐藏浏览器原生的密码可见性切换按钮 */
    input[type="password"]::-ms-reveal, input[type="password"]::-ms-clear { display: none !important; }
    input[type="password"]::-webkit-credentials-auto-fill-button { display: none !important; }
    /* 管理界面 — 文件上传框高度与对话框对齐、替换拖拽文字 */
    [data-testid="stFileUploader"] section { padding: 0 !important; min-height: 0 !important; }
    [data-testid="stFileUploader"] section > div:first-child { padding: calc(.5em - 1px) 8px !important; }
    [data-testid="stFileUploader"] span[data-testid="stFileUploaderDropzoneText"] { font-size: 0 !important; }
    [data-testid="stFileUploader"] span[data-testid="stFileUploaderDropzoneText"]::after { content: "上传图片" !important; font-size: 0.88rem !important; }
    [data-testid="stFileUploaderDropzone"] small { display: none !important; }
    [data-testid="stFileUploaderDropzone"] { min-height: 0 !important; }
    /* 管理界面 st.code() 代码块自动换行，防止长行撑破对话框 */
    [data-testid="stCodeBlock"] pre, [data-testid="stCodeBlock"] code {
        white-space: pre-wrap !important;
        word-break: break-word !important;
        overflow-wrap: break-word !important;
    }
    /* 移动端：上传框内容不换行，含拖拽区文字和已上传文件名 */
    @media (max-width: 768px) {
        [data-testid="stFileUploaderDropzone"],
        [data-testid="stFileUploaderDropzone"] * {
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
        }
        [data-testid="stFileUploaderFileData"] {
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

    # 启动清理
    if "cleanup_done" not in st.session_state:
        cleanup_old_sessions()
        delete_expired_docs()
        st.session_state.cleanup_done = True

    # ============================================================
    # 页面头部 + 管理入口（主内容区右上角，不再依赖侧栏）
    # ============================================================
    if st.session_state.admin_mode:
        st.markdown("""
        <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
            <h1 style="font-size:1.6rem; font-weight:700; margin-bottom:0.25rem;">管理控制台</h1>
            <p style="color:#888; font-size:0.9rem;">
                修改脚本规则 · 管理模板配置 · AI 对话编辑
            </p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("← 返回用户界面", use_container_width=True, key="header_return"):
            st.session_state.admin_mode = False
            st.session_state.admin_msgs = []
            st.rerun()

        admin_top = st.empty()
        with admin_top.container():
            with st.expander("查看可管理的内容类型", expanded=False):
                st.markdown("""
                <table style="font-size:0.82rem; width:100%; border-collapse:collapse;">
                <tr><td style="padding:4px 8px; width:6em; font-weight:600;">模板链接</td><td style="padding:4px 8px;">文件夹Token、混剪/口播模板ID、产品介绍库链接</td></tr>
                <tr><td style="padding:4px 8px; font-weight:600;">内容要求</td><td style="padding:4px 8px;">混剪/口播规则、产品介绍库、交付要求字段</td></tr>
                </table>
                """, unsafe_allow_html=True)

            with st.expander("修改管理密码", expanded=False):
                col_pw1, col_pw2, col_pw3 = st.columns(3)
                with col_pw1:
                    old_pw = st.text_input("当前密码", type="password", key="change_old_pw")
                with col_pw2:
                    new_pw = st.text_input("新密码", type="password", key="change_new_pw")
                with col_pw3:
                    new_pw_confirm = st.text_input("确认新密码", type="password", key="change_new_pw_confirm")
                _, btn_col = st.columns([2, 1])
                with btn_col:
                    if st.button("确认修改密码", type="primary", use_container_width=True, key="change_pw_btn"):
                        if old_pw != ADMIN_PASSWORD:
                            st.error("当前密码错误")
                        elif not new_pw:
                            st.error("新密码不能为空")
                        elif new_pw != new_pw_confirm:
                            st.error("两次输入的新密码不一致")
                        else:
                            import secrets
                            new_recovery = secrets.token_hex(6)
                            if save_admin_credentials(new_pw, new_recovery):
                                st.success("密码已修改！")
                                st.markdown("**新的恢复密钥（请立即复制保存）：**")
                                st.code(new_recovery, language=None)
                                st.caption("此密钥可用于忘记密码时重置，也可交给其他管理员完成权限交接。关闭后不可再次查看。")
                            else:
                                st.error("密码保存失败，请检查文件权限。")

        st.divider()
    else:
        st.markdown("""
        <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
            <h1 style="font-size:1.6rem; font-weight:700; margin-bottom:0.25rem;">短视频脚本生成系统</h1>
            <p style="color:#888; font-size:0.9rem;">
                粘贴抖音链接 &rarr; AI 分析 &rarr; 输出飞书文档
            </p>
        </div>
        """, unsafe_allow_html=True)

        # 管理入口 — 分割线上方（st.empty 防止展开时页面偏移）
        user_header = st.empty()
        with user_header.container():
            with st.expander("管理", expanded=False):
                admin_pw = st.text_input("密码", type="password", placeholder="管理员密码", key="admin_pw_input")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("进入后台", use_container_width=True, key="header_enter"):
                        if admin_pw == ADMIN_PASSWORD:
                            st.session_state.admin_mode = True
                            st.session_state.admin_msgs = []
                            st.rerun()
                        elif admin_pw:
                            st.error("密码错误")
                with c2:
                    with st.popover("忘记密码", use_container_width=True):
                        st.info("忘记密码功能开发中，敬请期待。如需重置，请删除 `config/admin.json` 文件恢复默认密码 `admin888`。")

        st.divider()

    # ============================================================
    # 分割线以下 — 可变内容区
    # ============================================================

    # 设置面板占位符 — step=0 时渲染输入面板，其他 step 自动清空以消除 ghost UI
    settings_placeholder = st.empty()
    if not st.session_state.admin_mode and st.session_state.step == 0:
        with settings_placeholder.container():
            render_input_panel()
    else:
        settings_placeholder.empty()

    # 可变内容占位符 — 每次 .container() 调用完全替换旧 widgets
    screen = st.empty()

    if st.session_state.admin_mode:
        with screen.container():
            render_admin_panel()
        return

    # 用户模式 — 所有可变 UI 包裹在 container 内
    with screen.container():
        _quality = st.session_state.get("quality", "standard")
        step_times = _STEP_TIMES.get(_quality, _STEP_TIMES["standard"])

        if st.session_state.step in (0, 5):
            delete_expired_docs()

        render_error()

        if st.session_state.step == 0:
            check_recovery()

        elif st.session_state.step == 1:
            render_progress_panel()
            _t1 = step_times.get(1, "约 10-30 秒")
            with st.status(f"第 1/4 步：提取视频（{_t1}）...", expanded=True) as status:
                step1_extract()
                if st.session_state.step == 2:
                    status.update(label="第 1/4 步：视频提取完成 ✓", state="complete")
            if st.session_state.step in (2, 0):
                st.rerun()

        elif st.session_state.step in (2, 3, 4):
            render_progress_panel()
            step_names = {2: f"AI 分析视频（{step_times.get(2, '约 2-4 分钟')}）", 3: f"生成脚本（{step_times.get(3, '约 10-30 秒')}）", 4: f"创建飞书文档（{step_times.get(4, '约 5-15 秒')}）"}
            step_labels_done = {2: "视频分析完成 ✓", 3: "脚本生成完成 ✓", 4: "飞书文档创建完成 ✓"}

            if st.session_state.step == 2:
                with st.status(f"第 2/4 步：{step_names[2]}...", expanded=True) as status:
                    step2_analyze()
                    if st.session_state.step == 3:
                        status.update(label=f"第 2/4 步：{step_labels_done[2]}", state="complete")
                if st.session_state.step in (3, 0):
                    st.rerun()

            elif st.session_state.step == 3:
                with st.status(f"第 3/4 步：{step_names[3]}...", expanded=True) as status:
                    step3_generate()
                    if st.session_state.step == 4:
                        status.update(label=f"第 3/4 步：{step_labels_done[3]}", state="complete")
                if st.session_state.step in (4, 0):
                    st.rerun()

            elif st.session_state.step == 4:
                with st.status(f"第 4/4 步：{step_names[4]}...", expanded=True) as status:
                    step4_feishu()
                    if st.session_state.step == 5:
                        status.update(label=f"第 4/4 步：{step_labels_done[4]}", state="complete")
                if st.session_state.step in (5, 0):
                    st.rerun()

        elif st.session_state.step == 5:
            render_result_panel()


if __name__ == "__main__":
    import os
    in_streamlit = False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            in_streamlit = True
            main()
    except Exception:
        pass
    if not in_streamlit:
        port = os.environ.get("PORT", "8501")
        os.execvpe(sys.executable, [
            sys.executable, "-m", "streamlit", "run", __file__,
            "--server.port", port,
            "--server.address", "0.0.0.0",
            "--server.headless", "true",
        ], os.environ)
