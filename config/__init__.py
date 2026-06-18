"""集中管理密钥、API端点、飞书资源、管理员密码。Prompt 构建见 src/prompt_builder.py。"""
import os
import json
import shutil
import base64
import hashlib
from pathlib import Path
from datetime import datetime

# ============================================================
# 内建加解密（纯 stdlib）
# ============================================================
_DECRYPT_PASSWORD = 'svas-2025-modelscope-deploy-key-internal'


def _derive_key(pwd: str, salt: bytes, length: int) -> bytes:
    key = hashlib.sha256(pwd.encode() + salt).digest()
    while len(key) < length:
        key += hashlib.sha256(key).digest()
    return key[:length]


def _decrypt(ciphertext: str) -> str:
    raw = base64.urlsafe_b64decode(ciphertext.encode())
    salt = raw[:16]
    encrypted = raw[16:]
    key = _derive_key(_DECRYPT_PASSWORD, salt, len(encrypted))
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()



# ============================================================
# API 密钥（环境变量 > 解密内置值）
# ============================================================
def _get_secret(key: str, encrypted_default: str = "") -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return val
    except Exception:
        pass
    return _decrypt(encrypted_default) if encrypted_default else ""


AGNES_API_KEY = _get_secret("AGNES_API_KEY",
    "GYcwcu7VJ3z9dUEDRYmPlFS6ojkjgu7iZhxP2Aj_2BA9Jo1GYcw5bqBVvtRSuSjIsOL6PNqmwHVYw2fzZUM25ndXwA==")
FEISHU_APP_ID = _get_secret("FEISHU_APP_ID",
    "fccXklZ-jvIo4CaTnmbEL5jNZHidZwgOCsx0DwaC6Tf3xxdL")
FEISHU_APP_SECRET = _get_secret("FEISHU_APP_SECRET",
    "lcuVw3ccI9NY0KUn4YJhAudEfF-REyQYnPVCYnvqf6kG903zweaA6q_P8U-mJ7rV")

# ============================================================
# API 端点
# ============================================================
AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"
AGNES_MODEL = "agnes-2.0-flash"
FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"


# ============================================================
# FFmpeg
# ============================================================
def get_ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return "ffmpeg"


FFMPEG_PATH = get_ffmpeg_path()

# ============================================================
# 请求 / 质量
# ============================================================
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
RETRY_MAX = 3
RETRY_BACKOFF = 0.5

# 质量配置已移至 src/prompt_builder.py


# ============================================================
# 飞书资源 ID（从 requirements.json 读取，回退默认值）
# ============================================================
def _read_req_json() -> dict:
    p = Path(__file__).parent / "requirements.json"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_folder_token() -> str:
    return _read_req_json().get("模板配置", {}).get(
        "文件夹Token", "nodcnfKha8zoI7HaoGIBOg7D4Hh")


def get_template_id(t: str) -> str:
    tc = _read_req_json().get("模板配置", {})
    key = "混剪模板ID" if t == "mix" else "口播模板ID"
    defaults = {"mix": "B1HtdfhjKo4g4QxgNNncCtVwnth",
                 "oral": "EbLGdZ2qYoQgpixsmQjc5EkjnNf"}
    return tc.get(key, defaults[t])


def generate_doc_title(script_type: str, seq: int = 1) -> str:
    """生成文档标题。

    Args:
        script_type: "mix"（混剪）或 "oral"（口播）
        seq: 序号（当天第几个）
    """
    today = datetime.now().strftime("%Y.%m.%d")
    type_name = "混剪脚本" if script_type == "mix" else "口播脚本"
    return f"{today}+{type_name}+{seq:03d}"


# ============================================================
# 配置加载
# ============================================================
_REQUIREMENTS_PATH = Path(__file__).parent / "requirements.json"

_DEFAULT_REQUIREMENTS = {
    "混剪": {
        "标题字数": "15-25字",
        "行数范围": [10, 16],
        "文案风格": "口语化，不要任何标点符号，同一表格框的内容，用自然换行分隔来表示语言停顿",
        "素材格式": "文件名.jpg 中文描述，格式如：功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包",
        "素材风格": "尽量使用动物、表情包等趣味素材",
        "广告": {
            "品牌": "鱼泡直聘",
            "描述": "软广植入",
            "位置": "约前50%位置处",
            "产品介绍来源": "从「产品介绍库」中，根据视频内容主题，选择最匹配的一段产品介绍文案",
        },
    },
    "口播": {
        "对话轮数范围": [8, 20],
        "角色格式": ["角色名", "对话内容【情绪标记】", "情绪词"],
        "情绪选项": ["疑惑", "好奇", "热心推荐", "有点发愁", "惊讶感叹", "真诚推荐",
                     "无奈摇头", "充满期待", "积极鼓励", "恍然大悟"],
        "情绪标记说明": "每轮对话末尾用【】标记情绪/动作/描述（2-4字均可），该部分在文档中用黄色高亮",
        "原片文案字数": "150-300字",
        "原片风格": "完整的原片叙述，纯文本，无角色对话",
        "图片素材格式": "每条以 emoji 开头 + 中文描述",
        "对话结构": "开场几轮抛出问题 → 中间几轮给出干货建议 → 后半段深化理解 → 结尾积极号召收尾",
    },
    "通用": {
        "语言": "中文",
        "返回格式": "严格的 JSON 格式，不要用 markdown 代码块包裹",
    },
    "交付要求": {
        "话题词数量": "4-5个",
        "话题词格式": "中文短语，用空格或#分隔，如：职场干货 #面试技巧 #求职",
    },
}


def load_requirements() -> dict:
    try:
        import streamlit as st
        if st.session_state.get("requirements"):
            return st.session_state.requirements
    except Exception:
        pass
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
    except Exception:
        return dict(_DEFAULT_REQUIREMENTS)



# ============================================================
# 边界控制常量（面向 ModelScope 单实例部署）
# ============================================================

# 飞书文档存活时间（秒），超时自动删除
DOC_TTL_SECONDS = 300  # 5 分钟

# 视频下载最长时长（秒），FFmpeg -t 参数
MAX_VIDEO_DURATION_SEC = 300  # 5 分钟

# 磁盘最小剩余空间（字节），低于此值拒绝下载
MIN_FREE_DISK_BYTES = 100 * 1024 * 1024  # 100 MB

# Whisper 转录超时（秒）
WHISPER_TIMEOUT_SEC = 300  # 5 分钟

# ============================================================
# 超时常量（秒）
# ============================================================
HTTP_TIMEOUT_SHORT = 10          # 短链接 HEAD 请求
HTTP_TIMEOUT_MEDIUM = 15         # 飞书认证、飞书 API 默认超时
HTTP_TIMEOUT_LONG = 20           # 抖音页面请求
SUBPROCESS_TIMEOUT_FFMPEG_DOWNLOAD = 180  # FFmpeg 视频下载
SUBPROCESS_TIMEOUT_AUDIO_DURATION = 30    # FFmpeg 音频时长检测
SUBPROCESS_TIMEOUT_AUDIO_EXTRACT = 60     # FFmpeg 音频提取
AI_TIMEOUT_GENERATE = 120        # 脚本生成 API
AI_TIMEOUT_REVIEW = 120          # 脚本审核 API

# 脚本目标字数（上下限，约束 AI 生成篇幅）
FIXED_TARGET_CHARS_MIX = (300, 400)   # 混剪：(下限, 上限)
FIXED_TARGET_CHARS_ORAL = (400, 500)  # 口播：(下限, 上限)
