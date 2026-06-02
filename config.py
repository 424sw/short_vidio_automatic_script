"""
集中管理所有常量、API 端点、模板 ID、Prompt 模板。
密钥从环境变量或 st.secrets 读取，绝不硬编码。
"""
import os
import json
import shutil
from pathlib import Path
from datetime import datetime

# ============================================================
# API 密钥（优先从环境变量读取，其次尝试 streamlit secrets）
# ============================================================

def _get_secret(key: str, default: str = "") -> str:
    """从环境变量或 streamlit secrets 获取密钥."""
    # 先尝试环境变量
    val = os.environ.get(key, "")
    if val:
        return val
    # 再尝试 streamlit secrets
    try:
        import streamlit as st
        return st.secrets.get(key, default)
    except Exception:
        return default


AGNES_API_KEY = _get_secret("AGNES_API_KEY", "sk-ZhwA3nuflKAXF2KkkcBJFj1oJwUk5GnyOoMTk2xkudKhX9L9")
FEISHU_APP_ID = _get_secret("FEISHU_APP_ID", "cli_aa97347bb5f9dbd7")
FEISHU_APP_SECRET = _get_secret("FEISHU_APP_SECRET", "UnGjpZgesVm4e0OKKkX5AEARIKiji4RC")

# ============================================================
# API 端点
# ============================================================

AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"
AGNES_MODEL = "agnes-2.0-flash"

FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"

# ============================================================
# 飞书资源 ID
# ============================================================

FOLDER_TOKEN = "nodcnfKha8zoI7HaoGIBOg7D4Hh"

TEMPLATE_IDS = {
    "mix": "B1HtdfhjKo4g4QxgNNncCtVwnth",    # 混剪模板
    "oral": "EbLGdZ2qYoQgpixsmQjc5EkjnNf",   # 口播模板
}

# ============================================================
# FFmpeg 路径（自动检测）
# ============================================================

def get_ffmpeg_path() -> str:
    """检测 FFmpeg 路径."""
    # 1. 先尝试 PATH 中的 ffmpeg
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 2. Windows 上的已知路径
    known_windows = r"C:\Users\15769\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
    if os.path.exists(known_windows):
        return known_windows
    # 3. 尝试 imageio-ffmpeg 内置的静态 ffmpeg（跨平台，部署环境首选）
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    # 4. 默认回退
    return "ffmpeg"


FFMPEG_PATH = get_ffmpeg_path()

# ============================================================
# Agent 配置
# ============================================================

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

# 请求重试配置
RETRY_MAX = 3
RETRY_BACKOFF = 0.5

# 并发帧分析配置
FRAME_ANALYSIS_WORKERS = 4

# ============================================================
# 输出质量预设
# ============================================================

QUALITY_PRESETS = {
    "fast": {
        "label": "🚀 快速",
        "fps": "1/10",
        "max_frames": 30,
        "workers": 4,
        "est_time": "约 30 秒",
        "description": "快速预览，适合尝鲜",
        "vision_detail": "简要描述画面关键元素",
    },
    "standard": {
        "label": "⚖️ 标准",
        "fps": "1/5",
        "max_frames": 60,
        "workers": 4,
        "est_time": "约 1-2 分钟",
        "description": "日常使用，平衡速度与质量",
        "vision_detail": "详细描述画面内容、文字、构图",
    },
    "fine": {
        "label": "🎯 精细",
        "fps": "1/2",
        "max_frames": 120,
        "workers": 3,
        "est_time": "约 3-5 分钟",
        "description": "精细分析，适合重要内容",
        "vision_detail": "尽可能详细地描述所有可见细节，包括人物微表情、画面色调、字体样式",
    },
}

# 默认值（兼容旧代码）
FRAME_EXTRACT_FPS = QUALITY_PRESETS["standard"]["fps"]
MAX_FRAMES = QUALITY_PRESETS["standard"]["max_frames"]

# ============================================================
# Prompt 模板
# ============================================================

VISION_PROMPT = """请详细描述这张视频关键帧的画面。包括：
1. 人物：数量、形象、动作、表情、穿搭
2. 场景：背景、环境
3. 文字内容：完整转录画面中所有可见文字
4. 画面构图：布局、色彩、视觉焦点

请用中文回复，尽可能详细。"""

SYNTHESIS_PROMPT = """你是一位专业的短视频内容分析师。以下是视频"{video_title}"的 {frame_count} 张关键帧的逐帧描述{audio_hint}。

请你综合分析所有信息，输出一份完整的视频理解报告，包含以下四个部分：

## 一、视频完整结构分析
将视频分为"开头钩子 → 中间展开 → 结尾总结"三个阶段，用时间线（XXs-XXs）描述各阶段内容和逻辑递进。

## 二、完整口播文案
{audio_instruction}

## 三、视频风格特点
分析视觉风格（配图类型、字体、配色）、节奏感（切换频率、结构推进方式）、语气/情绪基调。

## 四、关键信息点提取
列出 5-8 个核心信息点。

=== 逐帧描述 ===
{descriptions}

请用中文回复，结构清晰。"""

# ============================================================
# 要求配置加载 & 动态 Prompt 构建
# ============================================================

_REQUIREMENTS_PATH = Path(__file__).parent / "config" / "requirements.json"

# 内置 fallback 默认值（与 requirements.json 保持一致，文件丢失时使用）
_DEFAULT_REQUIREMENTS = {
    "通用": {
        "语言": "中文",
        "返回格式": "严格的 JSON 格式，不要用 markdown 代码块包裹",
        "交付要求_勿动": "不要修改模板内的任何格式和内容，仅修改封面要求中的标题占位符（黄色高亮）",
    },
    "混剪": {
        "标题字数": "15-25字，不含#标签",
        "行数范围": [10, 16],
        "文案风格": "口语化，不要任何标点符号，用自然换行分隔停顿",
        "素材格式": "文件名.jpg 中文描述，格式如：功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包",
        "素材风格": "尽量使用动物、表情包等趣味素材",
        "广告": {"品牌": "鱼泡直聘", "描述": "软广植入", "位置": "约前50%位置处"},
    },
    "口播": {
        "标题字数": "10字以内",
        "对话轮数": 20,
        "角色格式": ["角色名", "对话内容【情绪标记】", "情绪词"],
        "情绪选项": ["疑惑", "热心", "鼓励", "发愁", "惊讶", "推荐", "无奈", "期待"],
        "情绪标记说明": "每轮对话末尾用【】标记情绪",
        "原片文案字数": "150-300字",
        "原片风格": "完整的原片叙述，纯文本，无角色对话",
        "图片素材数量": 20,
        "图片素材格式": "每条以 emoji 开头 + 中文描述",
        "对话结构": "开场几轮抛出问题 → 中间几轮给出干货建议 → 后半段深化理解 → 结尾积极号召收尾",
    },
}


def load_requirements() -> dict:
    """加载要求配置。

    优先级：
    1. 用户在网页编辑器中修改的版本（st.session_state.requirements）
    2. requirements.json 文件
    3. 内置默认值（文件不存在或损坏时）

    部署到云端后用户无法编辑文件，可通过网页内 JSON 编辑器修改。
    """
    # 1. 优先：网页编辑器中的修改（仅当前会话生效）
    try:
        import streamlit as st
        if "requirements" in st.session_state and st.session_state.requirements is not None:
            return st.session_state.requirements
    except Exception:
        pass

    # 2. requirements.json 文件
    if not _REQUIREMENTS_PATH.exists():
        return dict(_DEFAULT_REQUIREMENTS)
    try:
        with open(_REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        merged = dict(_DEFAULT_REQUIREMENTS)
        for key in loaded:
            if key.startswith("_"):
                continue
            if key in merged and isinstance(merged[key], dict):
                merged[key] = {**merged[key], **loaded[key]}
            else:
                merged[key] = loaded[key]
        return merged
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        import logging
        logging.getLogger(__name__).warning(f"requirements.json 解析失败，使用默认值: {e}")
        return dict(_DEFAULT_REQUIREMENTS)


def build_mix_prompt(synthesis: str, custom_requirements: str = "") -> str:
    """构建混剪脚本 Prompt。

    默认规则从 requirements.json 读取。如有用户自定义要求，
    将其作为高优先级附加在 Prompt 最前面，AI 会自动合并。
    """
    req = load_requirements()
    m = req["混剪"]
    g = req["通用"]
    ad = m["广告"]
    lo, hi = m["行数范围"]

    override = _build_override_section(custom_requirements)

    return f"""你是一位短视频脚本策划专家。请根据以下视频分析，生成一个**混剪脚本**。
{override}
## 视频综合分析
{synthesis}

## 输出要求
返回{g["返回格式"]}：

{{{{
  "title": "脚本主标题（{m["标题字数"]}）",
  "rows": [
    ["口播文案第一句", "素材描述"],
    ["口播文案第二句", "素材描述"]
  ]
}}}}

## 内容要求
- 标题：{m["标题字数"]}
- 正文行数：{lo}-{hi} 行，根据视频内容灵活决定
- 文案风格：{m["文案风格"]}
- 素材列格式：{m["素材格式"]}
- 素材风格：{m["素材风格"]}
- 广告植入：{ad["品牌"]}{ad["描述"]}，放在{ad["位置"]}
- 语言：{g["语言"]}"""


def build_oral_prompt(synthesis: str, custom_requirements: str = "") -> str:
    """构建口播脚本 Prompt。

    默认规则从 requirements.json 读取。如有用户自定义要求，
    将其作为高优先级附加在 Prompt 最前面，AI 会自动合并。
    """
    req = load_requirements()
    o = req["口播"]
    g = req["通用"]
    emotions = "、".join(o["情绪选项"])
    role_fmt = "、".join(o["角色格式"])

    override = _build_override_section(custom_requirements)

    return f"""你是一位短视频脚本策划专家。请根据以下视频分析，生成一个**口播脚本**。
{override}
## 视频综合分析
{synthesis}

## 输出要求
返回{g["返回格式"]}：

{{{{
  "title": "脚本标题（{o["标题字数"]}）",
  "original_text": "原片完整文案（{o["原片风格"]}，{o["原片文案字数"]}）",
  "dialogs": [
    {o["角色格式"]},
    {o["角色格式"]}
  ],
  "images": [
    "图片素材描述1",
    "图片素材描述2"
  ]
}}}}

## 内容要求
- 标题：{o["标题字数"]}
- 对话轮数：{o["对话轮数"]} 轮 A/B 角色对话
- 每轮格式：{role_fmt}
- 情绪标记：{o["情绪标记说明"]}，可选情绪包括：{emotions}
- 原片文案：{o["原片风格"]}，{o["原片文案字数"]}
- 图片素材：{o["图片素材数量"]} 条，{o["图片素材格式"]}
- 对话结构：{o["对话结构"]}
- 语言：{g["语言"]}"""


def _build_override_section(custom_text: str) -> str:
    """构建用户自定义要求的高优先级覆盖段落。

    如果有冲突，以用户自定义要求为准。
    """
    if not custom_text or not custom_text.strip():
        return ""
    return f"""
## ⚠️ 用户自定义要求（最高优先级）
{custom_text.strip()}

**重要**：请将以上用户自定义要求与默认规则合并使用。如有冲突，以用户自定义要求为准。
"""


# ============================================================
# 辅助函数
# ============================================================

def get_quality_config(quality: str) -> dict:
    """获取质量预设配置.

    Args:
        quality: "fast" | "standard" | "fine"
    Returns:
        质量配置字典（fps, max_frames, workers, est_time 等）
    """
    if quality not in QUALITY_PRESETS:
        quality = "standard"
    return QUALITY_PRESETS[quality]


def get_vision_prompt(quality: str) -> str:
    """根据质量级别返回对应的逐帧分析 prompt."""
    preset = get_quality_config(quality)
    detail_level = preset.get("vision_detail", "详细描述画面内容")
    return f"""请描述这张视频关键帧的画面。{detail_level}。包括：
1. 人物：数量、形象、动作、表情、穿搭
2. 场景：背景、环境
3. 文字内容：完整转录画面中所有可见文字
4. 画面构图：布局、色彩、视觉焦点

请用中文回复。"""


def build_synthesis_prompt(frame_count: int, video_title: str,
                           descriptions: str, audio_transcript: str = "") -> str:
    """构建视频综合分析 prompt，包含音频转录（如有）.

    Args:
        frame_count: 关键帧数量
        video_title: 视频标题
        descriptions: 逐帧描述文本
        audio_transcript: 音频转录文字（可选）
    Returns:
        格式化后的综合 prompt
    """
    if audio_transcript:
        audio_hint = "，以及音频转录文字"
        audio_instruction = (
            "根据音频转录文字和画面内容，还原视频中的完整口播文案。"
            "标注大致时间节点和对应的画面特征。\n\n"
            f"音频转录文字：\n{audio_transcript}"
        )
    else:
        audio_hint = ""
        audio_instruction = (
            "根据画面中的文字和场景，推测视频中每一段的完整口播文案，"
            "标注大致时间节点和对应的画面特征。"
        )

    return SYNTHESIS_PROMPT.format(
        frame_count=frame_count,
        video_title=video_title,
        descriptions=descriptions,
        audio_hint=audio_hint,
        audio_instruction=audio_instruction,
    )


def generate_doc_title(script_type: str, seq: int = 1) -> str:
    """生成飞书文档标题，格式: 日期+类型+脚本+编号."""
    today = datetime.now().strftime("%Y.%m.%d")
    type_name = "混剪脚本" if script_type == "mix" else "口播脚本"
    return f"{today}+{type_name}+{seq:03d}"


def generate_simple_title(script_type: str) -> str:
    """生成简单标题（不含编号），用于页面文本."""
    today = datetime.now().strftime("%Y.%m.%d")
    type_name = "混剪脚本" if script_type == "mix" else "口播脚本"
    return f"{today}+{type_name}"
