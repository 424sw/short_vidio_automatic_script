"""AI 视频分析：FFmpeg 抽帧 + 音频转录 → AI 分析 → 综合理解。"""
import re
import base64
import logging
import subprocess
import uuid
import time
from pathlib import Path

from openai import OpenAI

from config import AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL, FFMPEG_PATH, load_requirements
from src.prompt_builder import get_quality_config, build_synthesis_prompt

logger = logging.getLogger(__name__)

# 本地模型
_MODEL_DIR = Path(__file__).parent.parent / "tools" / "models" / "faster-whisper-small"


class VideoAnalysisError(Exception):
    pass


def _make_client():
    return OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY, timeout=120.0)


# ============================================================
# 帧分析（全量单次调用）
# ============================================================

def _analyze_frames(frame_paths: list[str], quality: str = "standard") -> list[dict]:
    """所有帧打包进一次 API 调用。"""
    if not frame_paths:
        return []

    client = _make_client()
    detail = get_quality_config(quality)["vision_detail"]
    content_blocks = []
    names = []

    for fp in frame_paths:
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
        names.append(Path(fp).name)

    flist = "\n".join(f"{i+1}. {n}" for i, n in enumerate(names))
    prompt = (
        f"分析以下 {len(frame_paths)} 张关键帧。{detail}。\n\n"
        f"{flist}\n\n"
        f"逐帧描述，格式：--- 帧 {names[0]}\\n描述...\\n--- 帧 {names[-1]}\\n描述..."
    )
    content_blocks.append({"type": "text", "text": prompt})

    logger.info(f"分析 {len(frame_paths)} 帧（1 次 API）...")
    try:
        resp = client.chat.completions.create(
            model=AGNES_MODEL,
            messages=[{"role": "user", "content": content_blocks}],
            max_tokens=600 * len(frame_paths),
            timeout=180,
        )
        raw = resp.choices[0].message.content
        return _parse_frame_results(raw, names, frame_paths)
    except Exception as e:
        logger.error(f"帧分析失败: {e}")
        return [{"frame": n, "description": f"[失败: {e}]"} for n in names]


def _parse_frame_results(raw: str, names: list[str], paths: list[str]) -> list[dict]:
    """解析 LLM 帧分析结果，按文件名精确匹配。"""
    parts = re.split(r'(?:^|\n)\s*---+\s*(?:帧|Frame|frame)\s*(.*?)\n', raw)
    seen_names = set()
    results = []

    # 构建 标准化数字→文件名 映射（去前导零，如 "1" → "frame_001.jpg"）
    norm_num_map = {}
    for n in names:
        m = re.search(r'(\d+)', n)
        if m:
            norm_num_map[str(int(m.group(1)))] = n

    for i in range(1, len(parts), 2):
        ref = parts[i].strip() if i < len(parts) else ""
        desc = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if not desc:
            continue

        matched = None
        # 1) 精确文件名匹配
        for n in names:
            if n in seen_names:
                continue
            if n == ref or n in ref.split():
                matched = n
                break
        # 2) 子串匹配
        if not matched:
            for n in names:
                if n in seen_names:
                    continue
                if n in ref:
                    matched = n
                    break
        # 3) 标准化数字匹配（去前导零比对）
        if not matched:
            m = re.search(r'(\d+)', ref)
            if m:
                norm_digit = str(int(m.group(1)))
                matched = norm_num_map.get(norm_digit)

        if matched:
            if matched in seen_names:
                continue  # 跳过重复引用
            seen_names.add(matched)
        else:
            # 找第一个尚未被引用的帧作为回退
            for n in names:
                if n not in seen_names:
                    matched = n
                    seen_names.add(n)
                    break
            if not matched:
                matched = names[0]

        results.append({"frame": matched, "description": desc})

    # 补上未被引用的帧
    found = {r["frame"] for r in results}
    for n in names:
        if n not in found:
            results.append({"frame": n, "description": "[未解析到]"})

    order = {n: i for i, n in enumerate(names)}
    results.sort(key=lambda r: order.get(r["frame"], 999))
    return results


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
    ]
    for w in hotwords:
        if w not in words:
            words.append(w)

    # 构建 prompt（中文逗号分隔，模拟自然语流）
    return "这是一段关于求职招聘和职场话题的中文视频音频。涉及词汇：" + "，".join(words)


# ============================================================
# 音频转录
# ============================================================

def _transcribe_audio(audio_path: str) -> str:
    p = Path(audio_path)
    if not p.exists() or p.stat().st_size < 1000:
        return ""

    dur = _audio_duration(audio_path)
    if dur < 5:
        return ""

    model_path = str(_MODEL_DIR) if (
        (_MODEL_DIR / "model.bin").exists() or (_MODEL_DIR / "config.json").exists()
    ) else "small"
    local_only = model_path != "small"

    logger.info(f"转录音频（{'本地模型' if local_only else '在线下载'}，{dur:.0f} 秒）...")
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_path, device="cpu", compute_type="int8",
                            local_files_only=local_only)
        initial_prompt = _build_whisper_initial_prompt()
        logger.info(f"Whisper 领域上下文: {initial_prompt[:80]}...")
        segments, info = model.transcribe(audio_path, language="zh",
                                           beam_size=5, vad_filter=True,
                                           initial_prompt=initial_prompt)
        text = "".join(s.text.strip() for s in segments)
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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

    def extract_frames(self, video_path: str, output_dir: str,
                       quality: str = "standard") -> list[Path]:
        mm = get_quality_config(quality)
        max_frames = mm["max_frames"]

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("frame_*.jpg"):
            old.unlink()

        logger.info(f"抽帧 (max={max_frames}): {video_path}")
        cmd = [
            FFMPEG_PATH, "-i", str(video_path),
            "-vf", "fps=1",
            "-q:v", "2",
            str(out / "frame_%03d.jpg"),
            "-y",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise VideoAnalysisError(f"抽帧失败: {r.stderr[:300]}")

        frames = sorted(out.glob("frame_*.jpg"))
        if not frames:
            raise VideoAnalysisError("未抽到帧，视频可能太短")
        if len(frames) > max_frames:
            step = len(frames) / max_frames
            frames = [frames[int(i * step)] for i in range(max_frames)]

        logger.info(f"已抽取 {len(frames)} 帧")
        return frames

    def extract_audio(self, video_path: str, output_dir: str) -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        ap = out / "audio.mp3"

        r = subprocess.run([
            FFMPEG_PATH, "-i", str(video_path),
            "-vn", "-acodec", "libmp3lame",
            "-ar", "16000", "-ac", "1", "-q:a", "5",
            str(ap), "-y",
        ], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            logger.warning(f"音频提取失败: {r.stderr[:200]}")
            return ""
        return str(ap)

    def synthesize(self, frame_analyses: list[dict], metadata: dict,
                   audio_transcript: str = "", quality: str = "standard") -> str:
        failed_count = sum(
            1 for fa in frame_analyses if fa.get("description", "").startswith("[失败"))
        if failed_count == len(frame_analyses) and frame_analyses:
            raise VideoAnalysisError(
                f"所有 {len(frame_analyses)} 帧分析均失败，无法生成综合理解。"
                f"请检查 API 连接或视频质量。")
        elif failed_count:
            logger.warning("%d/%d 帧分析失败，继续综合理解", failed_count, len(frame_analyses))

        descriptions = "\n\n---\n\n".join(
            f"【{fa['frame']}】\n{fa['description']}" for fa in frame_analyses)
        prompt = build_synthesis_prompt(
            len(frame_analyses), metadata.get("title", ""),
            descriptions, audio_transcript)
        # 质量影响 synthesis 的输出长度
        synth_tokens = {"fast": 1500, "standard": 3000, "fine": 4000}.get(quality, 3000)
        client = _make_client()
        resp = client.chat.completions.create(
            model=AGNES_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=synth_tokens, timeout=120)
        return resp.choices[0].message.content

    def analyze(self, video_path: str, title: str, author: str = "",
                quality: str = "standard") -> dict:
        sid = uuid.uuid4().hex[:8]
        wd = Path("data") / "frames" / sid
        wd.mkdir(parents=True, exist_ok=True)

        try:
            # 抽帧 + 音频（并行可优化，当前串行）
            logger.info("抽帧 + 音频...")
            frames = self.extract_frames(video_path, str(wd), quality)
            audio_path = self.extract_audio(video_path, str(wd))

            # 转录（所有模式都执行，这是核心功能）
            audio_transcript = _transcribe_audio(audio_path) if audio_path else ""

            # 帧分析（quality 影响 vision_detail 和帧数）
            logger.info("帧分析...")
            frame_analysis = _analyze_frames([str(f) for f in frames], quality)

            # 综合（quality 影响 max_tokens）
            logger.info("综合理解...")
            synthesis = self.synthesize(frame_analysis,
                                        {"title": title, "author": author},
                                        audio_transcript, quality)

            return {
                "frame_analysis": frame_analysis,
                "synthesis": synthesis,
                "audio_transcript": audio_transcript,
                "quality": quality,
            }
        finally:
            import shutil
            try:
                shutil.rmtree(wd, ignore_errors=True)
            except Exception:
                pass
