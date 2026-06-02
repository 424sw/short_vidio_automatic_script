"""
AI 视频分析：FFmpeg 抽帧 + 音频转录 → agnes-2.0-flash 分析 → 综合理解。
"""
import os
import json
import base64
import logging
import subprocess
import uuid
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from config import (
    AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL,
    FFMPEG_PATH,
    VISION_PROMPT, SYNTHESIS_PROMPT,
    QUALITY_PRESETS,
    get_quality_config, get_vision_prompt, build_synthesis_prompt,
)

logger = logging.getLogger(__name__)


class VideoAnalysisError(Exception):
    """视频分析错误."""
    pass


def _make_client():
    """创建独立的 AI 客户端（线程安全）."""
    return OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY)


def _analyze_single_frame(frame_path: str, quality: str = "standard") -> dict:
    """在线程中独立分析一帧，不共享任何状态.

    Args:
        frame_path: 帧图片路径
        quality: 质量级别，决定 prompt 详细程度
    """
    client = _make_client()

    with open(frame_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    b64_uri = f"data:image/jpeg;base64,{b64}"

    vision_prompt = get_vision_prompt(quality)
    logger.info(f"分析帧 {Path(frame_path).name} ...")
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=AGNES_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": b64_uri}},
                        {"type": "text", "text": vision_prompt},
                    ],
                }],
                max_tokens=800,
                timeout=60,
            )
            return {
                "frame": Path(frame_path).name,
                "description": response.choices[0].message.content,
            }
        except Exception as e:
            logger.warning(f"帧 {Path(frame_path).name} 尝试 {attempt+1} 失败: {e}")
            if attempt == 1:
                return {
                    "frame": Path(frame_path).name,
                    "description": f"[分析失败: {e}]",
                }
            time.sleep(1)

    return {"frame": Path(frame_path).name, "description": "[分析失败]"}


# ============================================================
# 音频转录（faster-whisper）
# ============================================================

def _transcribe_audio(audio_path: str) -> str:
    """使用 faster-whisper 将音频转为文字.

    如果 faster-whisper 不可用或音频无效，回退到音频元信息分析。

    Args:
        audio_path: 音频文件路径

    Returns:
        转录文本或音频元信息字符串
    """
    p = Path(audio_path)
    if not p.exists() or p.stat().st_size < 1000:
        return ""

    # 获取音频时长
    duration_sec = _get_audio_duration(audio_path)
    if duration_sec < 5:
        logger.info(f"音频过短（{duration_sec:.1f}秒），跳过转录")
        return ""

    # 尝试 faster-whisper 转录
    try:
        from faster_whisper import WhisperModel

        logger.info(f"正在转录音频（时长 {duration_sec:.1f} 秒）...")
        # tiny 模型：~70MB，CPU 友好，中文效果不错
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            audio_path,
            language="zh",
            beam_size=5,
            vad_filter=True,  # 过滤静音段
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        full_text = "".join(text_parts)
        if full_text:
            logger.info(f"音频转录成功: {len(full_text)} 字符, 检测语言: {info.language}")
            return f"音频时长: {duration_sec:.1f}秒\n音频转录文字:\n{full_text}\n检测语言: {info.language}"
        else:
            logger.warning("音频转录结果为空")
            return f"音频时长: {duration_sec:.1f}秒（转录结果为空，可能为纯音乐/无声）"

    except ImportError:
        logger.warning("faster-whisper 未安装，使用音频元信息分析代替")
    except Exception as e:
        logger.warning(f"音频转录失败: {e}，使用音频元信息分析代替")

    # 回退：返回音频元信息
    audio_codec = _get_audio_codec(audio_path)
    return f"音频时长: {duration_sec:.1f}秒, 编码: {audio_codec}（未转录文字）"


def _get_audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）."""
    import re
    cmd = [FFMPEG_PATH, "-i", str(audio_path), "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stderr = result.stderr
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def _get_audio_codec(audio_path: str) -> str:
    """获取音频编码格式."""
    import re
    cmd = [FFMPEG_PATH, "-i", str(audio_path), "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        match = re.search(r"Stream #0:\d+.*?Audio:\s*(.*)", result.stderr)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return "未知"


# ============================================================
# VideoAnalyzer 类
# ============================================================

class VideoAnalyzer:
    """AI 视频分析器."""

    def __init__(self):
        pass  # 不持有共享客户端（线程安全）

    # ============================================================
    # FFmpeg 抽帧 & 音频提取
    # ============================================================

    def extract_frames(self, video_path: str, output_dir: str,
                       quality: str = "standard") -> list[Path]:
        """从视频中抽取关键帧.

        Args:
            video_path: 视频文件路径
            output_dir: 输出目录
            quality: 质量级别（fast / standard / fine）
        """
        preset = get_quality_config(quality)
        fps = preset["fps"]
        max_frames = preset["max_frames"]

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        for old in output.glob("frame_*.jpg"):
            old.unlink()

        logger.info(f"FFmpeg 抽帧 ({fps}): {video_path}")
        cmd = [
            FFMPEG_PATH, "-i", str(video_path),
            "-vf", f"fps={fps}",
            "-q:v", "2",
            str(output / "frame_%03d.jpg"),
            "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise VideoAnalysisError(f"FFmpeg 抽帧失败: {result.stderr[:300]}")

        frames = sorted(output.glob("frame_*.jpg"))
        if not frames:
            raise VideoAnalysisError("未抽到任何帧。视频可能太短（< 5 秒）。")

        if len(frames) > max_frames:
            step = len(frames) / max_frames
            frames = [frames[int(i * step)] for i in range(max_frames)]
            logger.warning(f"帧数过多，已均匀采样至 {len(frames)} 帧")

        logger.info(f"已抽取 {len(frames)} 帧（{preset['label']}模式，上限 {max_frames} 帧）")
        return frames

    def extract_audio(self, video_path: str, output_dir: str) -> str:
        """从视频中提取音频，返回音频文件路径."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        audio_path = output / "audio.mp3"

        logger.info(f"提取音频: {video_path}")
        cmd = [
            FFMPEG_PATH, "-i", str(video_path),
            "-vn",               # 不要视频
            "-acodec", "libmp3lame",
            "-ar", "16000",      # 16kHz 采样率
            "-ac", "1",          # 单声道
            "-q:a", "5",
            str(audio_path),
            "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning(f"音频提取失败（视频可能无音频轨）: {result.stderr[:200]}")
            return ""

        size_kb = audio_path.stat().st_size / 1024
        logger.info(f"音频已提取: {size_kb:.1f}KB")
        return str(audio_path)

    # ============================================================
    # AI 分析
    # ============================================================

    def analyze_all_frames(self, frame_paths: list[Path],
                           quality: str = "standard") -> list[dict]:
        """并发分析所有帧（每个线程独立创建客户端）.

        Args:
            frame_paths: 帧图片路径列表
            quality: 质量级别
        """
        preset = get_quality_config(quality)
        workers = preset["workers"]

        logger.info(f"并发分析 {len(frame_paths)} 帧 (workers={workers})...")

        results = []
        frame_str_paths = [str(fp) for fp in frame_paths]

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(_analyze_single_frame, fp, quality): i
                for i, fp in enumerate(frame_str_paths)
            }
            for future in as_completed(future_to_idx):
                result = future.result()
                results.append(result)

        results.sort(key=lambda r: r["frame"])
        success = sum(1 for r in results if not r["description"].startswith("["))
        logger.info(f"帧分析完成: {success}/{len(results)} 成功")
        return results

    def synthesize_video(self, frame_analyses: list[dict],
                         metadata: dict, audio_transcript: str = "") -> str:
        """汇总逐帧描述 + 音频转录，生成综合视频理解.

        Args:
            frame_analyses: 逐帧分析结果列表
            metadata: 视频元信息 {"title": str, "author": str}
            audio_transcript: 音频转录文字（如有）
        """
        logger.info("AI 综合理解...")

        descriptions = "\n\n---\n\n".join(
            f"【{fa['frame']}】\n{fa['description']}"
            for fa in frame_analyses
        )

        # 使用 build_synthesis_prompt 构建包含音频信息的 prompt
        prompt = build_synthesis_prompt(
            frame_count=len(frame_analyses),
            video_title=metadata.get("title", ""),
            descriptions=descriptions,
            audio_transcript=audio_transcript,
        )

        client = _make_client()
        response = client.chat.completions.create(
            model=AGNES_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
            timeout=120,
        )
        return response.choices[0].message.content

    # ============================================================
    # 完整分析流程
    # ============================================================

    def analyze(self, video_path: str, title: str, author: str = "",
                quality: str = "standard", progress_callback=None) -> dict:
        """完整视频分析流程.

        Args:
            video_path: 视频文件路径
            title: 视频标题
            author: 视频作者
            quality: 质量级别 ("fast" | "standard" | "fine")
            progress_callback: 进度回调函数 func(msg: str)

        Returns:
            {frame_analysis: [...], synthesis: str, audio_transcript: str, quality: str}
        """
        preset = get_quality_config(quality)
        session_id = uuid.uuid4().hex[:8]
        work_dir = Path("data") / "frames" / session_id
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: 抽帧 + 提取音频（并行）
            logger.info(f"Step 1/4: 抽取关键帧 + 音频提取 ({preset['label']}模式)...")
            frame_paths = self.extract_frames(video_path, str(work_dir), quality)
            audio_path = self.extract_audio(video_path, str(work_dir))

            # 转录音频
            audio_transcript = ""
            if audio_path:
                if progress_callback:
                    progress_callback("正在进行语音转文字...")
                audio_transcript = _transcribe_audio(audio_path)
                if progress_callback and audio_transcript:
                    preview = audio_transcript.split("\n")[-1][:80] if "\n" in audio_transcript else audio_transcript[:80]
                    progress_callback(f"语音识别完成: {preview}...")

            # Step 2: 逐帧分析
            logger.info(f"Step 2/4: AI 分析 {len(frame_paths)} 帧...")
            if progress_callback:
                progress_callback(
                    f"正在 AI 分析 {len(frame_paths)} 个关键帧"
                    f"（{preset['label']}模式，{preset['est_time']}）..."
                )
            frame_analyses = self.analyze_all_frames(frame_paths, quality)

            # Step 3: 综合理解
            logger.info("Step 3/4: AI 综合理解...")
            if progress_callback:
                progress_callback("正在生成综合理解报告...")
            synthesis = self.synthesize_video(frame_analyses, {
                "title": title, "author": author,
            }, audio_transcript)

            # Step 4: 清理临时文件
            logger.info("Step 4/4: 清理临时文件...")

            return {
                "frame_analysis": frame_analyses,
                "synthesis": synthesis,
                "audio_transcript": audio_transcript,
                "quality": quality,
            }
        finally:
            import shutil
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass
