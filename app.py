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
    initial_sidebar_state="collapsed",
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
    registry = _load_doc_registry()
    if not registry:
        return
    now = time.time()
    expired = [did for did, m in registry.items() if now - m["created_at"] > _DOC_TTL]
    if not expired:
        return
    try:
        client = FeishuClient()
    except Exception:
        return
    for doc_id in expired:
        try:
            if client.delete_document(doc_id):
                del registry[doc_id]
        except Exception:
            pass
    _save_doc_registry(registry)


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
        custom = st.text_area(
            "文字描述",
            value=st.session_state.get("custom_requirements", ""),
            height=80,
            placeholder="例：标题短一点，只要 8 行，不要广告，语气轻松活泼",
            key="custom_req_input",
        )
        uploaded_files = st.file_uploader(
            "上传图片",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key="req_image_uploader",
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
        st.session_state.step = 1
        st.session_state.elapsed_start = time.time()
        st.rerun()


# ============================================================
# 面板二：进度
# ============================================================

def render_progress_panel():
    step = st.session_state.step
    step_labels = {1: "提取视频", 2: "分析视频", 3: "生成脚本", 4: "创建飞书文档"}

    elapsed = time.time() - st.session_state.elapsed_start
    e_str = f"{int(elapsed // 60)} 分 {int(elapsed % 60)} 秒" if elapsed >= 60 else f"{int(elapsed)} 秒"

    preset = get_quality_config(st.session_state.quality)
    msg = st.session_state.get("status_msg", "")

    filled = "●" * (step - 1)
    current = "◉"
    empty = "○" * (4 - step)
    bar = "  ".join([filled, current, empty]) if filled else "  ".join([current, empty])

    st.markdown(f"""
    <div style="text-align:center; margin: 3.5rem 0 2rem 0;">
        <div style="font-size: 1.5em; letter-spacing: 4px; margin-bottom: 0.75rem;">{bar}</div>
        <div style="font-size: 1.05em; color: #333; margin: 0.5rem 0;">
            第 {step} 步 &middot; {step_labels.get(step, '...')}
        </div>
        <div style="color: #999; font-size: 0.85em;">
            已用时 {e_str} &nbsp; | &nbsp; 预计 {preset['est_time']}
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

    if st.session_state.get("doc_ids"):
        st.warning("文档将在 5 分钟后自动删除，请尽快保存副本")

    st.markdown(f"### 已生成 {len(script_jsons)} 个脚本")

    for i, (script, doc_url) in enumerate(zip(script_jsons, doc_urls)):
        if not doc_url:
            continue
        title = script.get("title", f"脚本{i+1}") if script else f"脚本{i+1}"
        label = f"脚本 {i+1}"

        st.markdown(f"""
        <div style="border: 1px solid #d0d5dd; border-radius: 8px; padding: 20px; margin: 12px 0;">
            <p style="font-size: 1em; font-weight: 600; margin: 0 0 8px 0; color: #333;">{label}</p>
            <a href="{doc_url}" target="_blank" style="font-size: 0.9em; color: #1a56db;
                word-break: break-all; text-decoration: none;">{doc_url}</a>
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
- **模板配置**：飞书文件夹Token、混剪模板ID、口播模板ID（如替换模板文档时使用）
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


def render_admin_panel():
    # 先渲染 sidebar（让退出按钮在侧栏）
    with st.sidebar:
        st.markdown("### 管理控制台")
        if st.button("← 返回用户界面", use_container_width=True):
            st.session_state.admin_mode = False
            st.session_state.admin_msgs = []
            st.rerun()
        st.divider()
        st.caption("修改不会自动同步到 ModelScope 创空间。若需更新线上版本，请 push 代码。")

    st.title("配置管理")
    st.caption("用自然语言修改脚本规则 → AI 翻译 → 确认生效")

    # 对话历史
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
                    if st.button("✅ 确认保存", key=f"adm_save_{i}", type="primary"):
                        with open(_ADMIN_REQ_PATH, "w", encoding="utf-8") as f:
                            json.dump(cfg, f, ensure_ascii=False, indent=2)
                        msg["resolved"] = True
                        msg["content"] += "\n\n✅ 已保存。重启 Streamlit 后生效。"
                        st.rerun()
                with a2:
                    if st.button("撤销", key=f"adm_undo_{i}"):
                        msg["resolved"] = True
                        msg["content"] += "\n\n已撤销。"
                        st.rerun()

    if prompt := st.chat_input("例如：混剪行数改成 8-12 行、广告品牌改成小红书..."):
        st.session_state.admin_msgs.append({"role": "user", "content": prompt})
        with st.spinner("..."):
            try:
                current = json.loads(_ADMIN_REQ_PATH.read_text(encoding="utf-8"))
            except Exception:
                current = {}
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
        client = FeishuClient()
        doc_urls, doc_ids = [], []
        for i, script in enumerate(scripts):
            seq = i + 1
            if total > 1:
                st.session_state.status_msg = f"正在创建飞书文档（{seq}/{total}）..."
            else:
                st.session_state.status_msg = "正在创建飞书文档..."

            result = client.create_and_fill(
                st.session_state.script_type, script,
                st.session_state.video_url, st.session_state.video_title,
                seq=seq,
            )
            doc_urls.append(result["url"])
            doc_ids.append(result["doc_id"])

        st.session_state.doc_urls = doc_urls
        st.session_state.doc_url = doc_urls[0]
        st.session_state.doc_id = doc_ids[0]
        st.session_state.doc_ids = doc_ids
        st.session_state.doc_created_at = time.time()
        st.session_state.step = 5
        st.session_state.generation_complete = True
        st.session_state.status_msg = ""

        # 登记文档（5 分钟后自动删除）
        register_docs(doc_ids, doc_urls)
        # 清理旧 checkpoint
        delete_checkpoint(st.session_state.video_url)
        logger.info(f"飞书文档已创建: {len(doc_urls)} 个")
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
    # 隐藏标题悬浮锚点图标 + 帮助问号
    st.markdown("""
    <style>
    .stMarkdown h1 a, .stMarkdown h2 a, .stMarkdown h3 a { display: none; }
    </style>
    """, unsafe_allow_html=True)

    # 启动清理
    if "cleanup_done" not in st.session_state:
        cleanup_old_sessions()
        delete_expired_docs()
        st.session_state.cleanup_done = True

    # 管理后台模式 — 完全替换主界面
    if st.session_state.admin_mode:
        render_admin_panel()
        return

    # 每次页面加载时尝试清理过期文档
    if st.session_state.step in (0, 5):
        delete_expired_docs()

    # 头部
    st.markdown("""
    <div style="text-align:center; padding: 1rem 0 0.5rem 0;">
        <h1 style="font-size:1.6rem; font-weight:700; margin-bottom:0.25rem;">短视频脚本生成系统</h1>
        <p style="color:#888; font-size:0.9rem;">
            粘贴抖音链接 &rarr; AI 分析 &rarr; 输出飞书文档
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    render_error()

    # 管理入口（侧栏）
    with st.sidebar:
        with st.expander("⚙️ 管理", expanded=False):
            admin_pw = st.text_input("管理密码", type="password", placeholder="输入密码")
            if st.button("进入管理后台", use_container_width=True):
                if admin_pw == ADMIN_PASSWORD:
                    st.session_state.admin_mode = True
                    st.session_state.admin_msgs = []
                    st.rerun()
                elif admin_pw:
                    st.error("密码错误")

    if st.session_state.step == 0:
        check_recovery()
        render_input_panel()

    elif st.session_state.step == 1:
        # 全部管道在 st.status() 内一气呵成，单次 render 完成
        # st.status() 流式传输进度，浏览器实时看到，不会出现 ghost 按钮
        with st.status("处理中", expanded=True) as status:
            status.write("提取视频...")
            step1_extract()
            status.write("分析视频...")
            step2_analyze()
            status.write("生成脚本...")
            step3_generate()
            status.write("创建飞书文档...")
            step4_feishu()
            status.update(label="全部完成", state="complete")
        st.rerun()

    elif st.session_state.step in (2, 3, 4):
        # 从 checkpoint 恢复，逐步骤执行
        render_progress_panel()
        if st.session_state.step == 2:
            step2_analyze()
            st.rerun()
        elif st.session_state.step == 3:
            step3_generate()
            st.rerun()
        elif st.session_state.step == 4:
            step4_feishu()
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
