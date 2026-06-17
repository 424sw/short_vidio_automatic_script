"""视频分析：音频提取 + 语音转录。"""
import gc
import re
import logging
import subprocess
from pathlib import Path

from config import FFMPEG_PATH, load_requirements, \
    WHISPER_TIMEOUT_SEC, SUBPROCESS_TIMEOUT_AUDIO_DURATION, SUBPROCESS_TIMEOUT_AUDIO_EXTRACT

logger = logging.getLogger(__name__)

# 本地模型
_MODEL_DIR = Path(__file__).parent.parent / "tools" / "models" / "faster-whisper-small"


class VideoAnalysisError(Exception):
    pass



# ============================================================
# Whisper 领域上下文（改善网络热词/行业术语识别）
# ============================================================

def _build_whisper_initial_prompt() -> str:
    """构建领域上下文，注入 faster-whisper 以提升行业术语/网络热词识别率。

    Whisper 的 initial_prompt 参数为解码器提供先验词汇偏好，
    模型在识别时更倾向匹配 prompt 中出现过的词，从而减少「音近错字」。
    """
    try:
        req = load_requirements()
    except Exception:
        req = {}

    # 收集品牌名
    words = []
    for section in ("混剪", "口播"):
        brand = req.get(section, {}).get("广告", {}).get("品牌", "")
        if brand and brand not in words:
            words.append(brand)

    # 从产品介绍库中提取关键术语
    products = req.get("产品介绍库", [])
    for p in products:
        text = p.get("文案", "") + p.get("主题", "") + p.get("适用场景", "")
        for term in ["求职", "招聘", "直招", "蓝领", "岗位", "面试", "实习",
                     "校招", "兼职", "灵活用工", "副业", "培训", "技能",
                     "工厂", "工地", "制造业", "日结", "周结", "月结"]:
            if term in text and term not in words:
                words.append(term)

    # ==== 互联网热词库（基于 2025-2026 真实流行语） ====
    # 这些词汇不在 Whisper 通用训练集中，极易被识别成音近错字。
    # 如「主包」→ 主播、「家人们」→ 加入们、「上岸」→ 上暗。
    hotwords = [
        # 通用网络用语
        "主包", "家人们", "宝子们", "老铁", "集美",
        "绝绝子", "yyds", "emo", "破防", "下头", "上头",
        "真香", "芭比Q", "栓Q", "离谱", "无语",
        "摆烂", "躺平", "摸鱼", "内卷", "画饼", "白嫖",
        "种草", "拔草", "安利", "避坑", "避雷", "捡漏",
        "韭菜", "割韭菜", "智商税", "显眼包", "社死",
        "天花板", "平替", "闭眼入", "宝藏", "翻车", "塌房",
        # 求职 / 职场
        "上岸", "大厂", "小厂", "offer", "内推", "海投",
        "面经", "群面", "单面", "简历", "投递",
        "打工人", "社畜", "裸辞", "跳槽", "转正", "试用期",
        "五险一金", "薪资", "待遇", "副业", "远程办公",
        "搞钱", "来财",
        "牛马", "一身班味", "精神退休", "情绪价值",
        "全职儿女", "数字游民", "主理人",
        # 短视频平台 / 内容创作
        "素材", "文案", "口播", "混剪", "涨粉", "取关",
        # 广告品牌名
        "鱼泡直聘", 
    ]
    for w in hotwords:
        if w not in words:
            words.append(w)

    # 构建 prompt（中文逗号分隔，模拟自然语流）
    return "这是一段关于求职招聘和职场话题的中文视频音频。涉及词汇：" + "，".join(words)


# ============================================================
# 音频转录
# ============================================================

def _transcribe_audio(audio_path: str, quality: str = "standard") -> str:
    p = Path(audio_path)
    if not p.exists() or p.stat().st_size < 1000:
        return ""

    dur = _audio_duration(audio_path)
    if dur < 5:
        return ""

    # 精细模式：float32 + 更大 beam size，识别更准但更慢
    use_fine = (quality == "fine")
    compute = "float32" if use_fine else "int8"
    beam = 10 if use_fine else 5
    quality_label = "精细" if use_fine else "标准"
    logger.info(f"转录音频（{quality_label}，{dur:.0f} 秒，compute={compute} beam={beam}）...")
    try:
        from faster_whisper import WhisperModel
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

        model_path = str(_MODEL_DIR) if (
            (_MODEL_DIR / "model.bin").exists() or (_MODEL_DIR / "config.json").exists()
        ) else "small"
        local_only = model_path != "small"
        model = WhisperModel(model_path, device="cpu", compute_type=compute,
                            local_files_only=local_only)
        initial_prompt = _build_whisper_initial_prompt()
        logger.info(f"Whisper 领域上下文: {initial_prompt[:80]}...")

        try:
            # 在独立线程中执行转录，超时兜底
            def _do_transcribe():
                segments, info = model.transcribe(audio_path, language="zh",
                                                   beam_size=beam, vad_filter=True,
                                                   initial_prompt=initial_prompt)
                text = "".join(s.text.strip() for s in segments)
                return text, info

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_transcribe)
                try:
                    text, info = future.result(timeout=WHISPER_TIMEOUT_SEC)
                except FutureTimeout:
                    logger.warning(f"Whisper 转录超时（{WHISPER_TIMEOUT_SEC}秒），返回部分结果")
                    return f"音频时长: {dur:.1f}秒\n音频转录:\n（转录超时，音频时长 {dur:.0f} 秒）\n语言: zh\n⚠️ 转录超时"
        finally:
            del model
            gc.collect()

        if text:
            return f"音频时长: {dur:.1f}秒\n音频转录:\n{text}\n语言: {info.language}\n⚠️ 若转录为繁体，请在下游 AI 步骤中要求转换为简体中文"
    except ImportError:
        logger.warning("faster-whisper 未安装")
    except Exception as e:
        logger.warning(f"转录失败: {e}")

    return f"音频时长: {dur:.1f}秒（未转录）"


def _audio_duration(audio_path: str) -> float:
    cmd = [FFMPEG_PATH, "-i", str(audio_path), "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_AUDIO_DURATION)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except subprocess.TimeoutExpired:
        logger.warning("音频时长检测超时: %s", audio_path)
    except Exception as e:
        logger.warning("音频时长检测失败: %s", e)
    return 0.0


# ============================================================
# VideoAnalyzer
# ============================================================

class VideoAnalyzer:

    def extract_audio(self, video_path: str, output_dir: str) -> str:
        """从视频中提取音频为 mp3，返回音频路径。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        ap = out / "audio.mp3"

        r = subprocess.run([
            FFMPEG_PATH, "-y",
            "-i", str(video_path),
            "-vn", "-acodec", "libmp3lame",
            "-ar", "16000", "-ac", "1", "-q:a", "5",
            str(ap),
        ], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_AUDIO_EXTRACT)
        if r.returncode != 0:
            logger.warning(f"音频提取失败: {r.stderr[:200]}")
            return ""
        return str(ap)

    def analyze(self, video_path: str, title: str, author: str = "",
                session_dir: str = None, quality: str = "standard") -> dict:
        """分析视频：提取音频 + 语音转录。

        所有临时文件写入 session_dir，由调用方统一管理生命周期。
        quality: "standard" → int8 + beam=5（快）；"fine" → float32 + beam=10（更准）。
        """
        if session_dir:
            wd = Path(session_dir)
        else:
            wd = Path("data") / "tmp"
        wd.mkdir(parents=True, exist_ok=True)

        # 1. 提取音频
        logger.info("提取音频...")
        audio_path = self.extract_audio(video_path, str(wd))

        # 2. 语音转录
        audio_transcript = _transcribe_audio(audio_path, quality=quality) if audio_path else ""

        return {
            "audio_transcript": audio_transcript,
        }
