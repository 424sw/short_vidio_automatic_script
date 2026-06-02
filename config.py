"""
集中管理所有常量、API 端点、模板 ID、Prompt 模板。
密钥从环境变量或 st.secrets 读取，绝不硬编码。
"""
import os
import shutil
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
    # 3. 默认回退
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
FRAME_EXTRACT_FPS = "1/5"
MAX_FRAMES = 60  # 最大帧数（5分钟 / 5秒 = 60）

# ============================================================
# Prompt 模板
# ============================================================

VISION_PROMPT = """请详细描述这张视频关键帧的画面。包括：
1. 人物：数量、形象、动作、表情、穿搭
2. 场景：背景、环境
3. 文字内容：完整转录画面中所有可见文字
4. 画面构图：布局、色彩、视觉焦点

请用中文回复，尽可能详细。"""

SYNTHESIS_PROMPT = """你是一位专业的短视频内容分析师。以下是视频"{video_title}"的 {frame_count} 张关键帧的逐帧描述。

请你综合分析这些帧，输出一份完整的视频理解报告，包含以下四个部分：

## 一、视频完整结构分析
将视频分为"开头钩子 → 中间展开 → 结尾总结"三个阶段，用时间线（XXs-XXs）描述各阶段内容和逻辑递进。

## 二、推测的完整口播文案
根据画面中的文字和场景，推测视频中每一段的完整口播文案，标注大致时间节点和对应的画面特征。

## 三、视频风格特点
分析视觉风格（配图类型、字体、配色）、节奏感（切换频率、结构推进方式）、语气/情绪基调。

## 四、关键信息点提取
列出 5-8 个核心信息点。

=== 逐帧描述 ===
{descriptions}

请用中文回复，结构清晰。"""

MIX_SCRIPT_PROMPT = """你是一位短视频脚本策划专家。请根据以下视频分析，生成一个**混剪脚本**。

## 视频综合分析
{synthesis}

## 输出要求
返回严格的 JSON 格式（不要用 markdown 代码块包裹）：

{{
  "title": "脚本主标题（不含#标签，15-25字）",
  "rows": [
    ["口播文案第一句（不要标点符号，用换行分隔）", "素材描述（格式：表情包.jpg 中文描述）"],
    ["口播文案第二句", "素材描述"]
  ]
}}

## 规则
- 内容列：口播文案文本，不要标点符号，用自然换行分隔停顿
- 素材列：格式为"描述关键词.jpg 详细中文描述"，如"功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包"，尽量使用动物、表情包等趣味素材
- 共 10-16 行，根据视频内容决定
- 鱼泡直聘软广放在 ≈50% 位置
- 返回纯 JSON，不要 markdown 代码块"""

ORAL_SCRIPT_PROMPT = """你是一位短视频脚本策划专家。请根据以下视频分析，生成一个**口播脚本**。

## 视频综合分析
{synthesis}

## 输出要求
返回严格的 JSON 格式（不要用 markdown 代码块包裹）：

{{
  "title": "脚本标题（10字以内）",
  "original_text": "原片完整文案（纯叙述文本，无角色对话，150-300字）",
  "dialogs": [
    ["角色A名称", "对话内容【情绪标记】", "情绪"],
    ["角色B名称", "对话内容【情绪标记】", "情绪"]
  ],
  "images": [
    "emoji 图片描述1",
    "emoji 图片描述2"
  ]
}}

## 规则
- dialogs 包含 20 轮 A/B 角色对话
- 每轮对话末尾用【】标记情绪（如【疑惑】【热心】【鼓励】【发愁】【惊讶】【推荐】）
- A/B 角色名称可用"求职者"/"导师"或根据视频内容自定义
- original_text 是完整的原片叙述
- images 是 20 条对应的图片素材描述，每条以 emoji 开头
- 对话要有剧情推进感，前几轮抛出问题，中间给出干货建议，后半段深化理解，最后以积极号召收尾
- 返回纯 JSON，不要 markdown 代码块"""

# ============================================================
# 辅助函数
# ============================================================

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
