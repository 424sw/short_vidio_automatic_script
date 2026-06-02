"""
AI 视频分析：FFmpeg 抽帧 + 音频提取 → agnes-2.0-flash 分析 → 综合理解。
"""
import os
import json
import base64
import logging
import subprocess
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from config import (
    AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL,
    FFMPEG_PATH,
    VISION_PROMPT, SYNTHESIS_PROMPT,
    FRAME_ANALYSIS_WORKERS, FRAME_EXTRACT_FPS, MAX_FRAMES,
)

logger = logging.getLogger(__name__)


class VideoAnalysisError(Exception):
    """视频分析错误."""
    pass


def _make_client():
    """创建独立的 AI 客户端（线程安全）."""
    return OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY)


def _analyze_single_frame(frame_path: str) -> dict:
    """在线程中独立分析一帧，不共享任何状态."""
    client = _make_client()

    with open(frame_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    b64_uri = f"data:image/jpeg;base64,{b64}"

    logger.info(f"分析帧 {Path(frame_path).name} ...")
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=AGNES_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": b64_uri}},
                        {"type": "text", "text": VISION_PROMPT},
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
            import time
            time.sleep(1)

    return {"frame": Path(frame_path).name, "description": "[分析失败]"}


def _transcribe_audio(audio_path: str) -> str:
    """从音频文件提取文字（使用 ffmpeg + 简单分析）.

    由于没有专业的语音转文字 API，当前方案:
    1. 用 ffmpeg 提取音频
    2. 分析音频属性（时长、采样率等）
    3. 返回音频元信息，供 AI 在综合分析中参考

    未来可替换为 whisper / 阿里云语音识别等专业服务。
    """
    p = Path(audio_path)
    if not p.exists():
        return ""

    # 获取音频时长等元信息
    cmd = [
        FFMPEG_PATH, "-i", str(audio_path),
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    stderr = result.stderr

    # 解析 duration
    import re
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if duration_match:
        h, m, s = duration_match.groups()
        duration_sec = int(h) * 3600 + int(m) * 60 + float(s)
        audio_info = f"音频时长: {duration_sec:.1f}秒"
    else:
        audio_info = "音频已提取（无法解析时长）"

    # 分析音频流信息
    audio_match = re.search(r"Stream #0:(\d+).*?Audio:\s*(.*)", stderr)
    if audio_match:
        audio_info += f", 编码格式: {audio_match.group(2).strip()}"

    logger.info(f"音频分析: {audio_info}")
    return audio_info


class VideoAnalyzer:
    """AI 视频分析器."""

    def __init__(self):
        pass  # 不再持有共享客户端

    # ============================================================
    # FFmpeg 抽帧 & 音频提取
    # ============================================================

    def extract_frames(self, video_path: str, output_dir: str,
                       fps: str = FRAME_EXTRACT_FPS) -> list[Path]:
        """从视频中抽取关键帧."""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        for old in output.glob("frame_*.jpg"):
            old.unlink()

        logger.info(f"FFmpeg 抽帧: {video_path}")
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
            raise VideoAnalysisError("未抽到任何帧。视频可能太短（< 5秒）。")

        if len(frames) > MAX_FRAMES:
            step = len(frames) / MAX_FRAMES
            frames = [frames[int(i * step)] for i in range(MAX_FRAMES)]
            logger.warning(f"帧数过多，已均匀采样至 {len(frames)} 帧")

        logger.info(f"已抽取 {len(frames)} 帧")
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

    def analyze_all_frames(self, frame_paths: list[Path]) -> list[dict]:
        """并发分析所有帧（每个线程独立创建客户端）."""
        logger.info(f"并发分析 {len(frame_paths)} 帧 (workers={FRAME_ANALYSIS_WORKERS})...")

        results = []
        frame_str_paths = [str(fp) for fp in frame_paths]

        with ThreadPoolExecutor(max_workers=FRAME_ANALYSIS_WORKERS) as executor:
            future_to_idx = {
                executor.submit(_analyze_single_frame, fp): i
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
                         metadata: dict, audio_info: str = "") -> str:
        """汇总逐帧描述 + 音频信息，生成综合视频理解."""
        logger.info("AI 综合理解...")

        descriptions = "\n\n---\n\n".join(
            f"【{fa['frame']}】\n{fa['description']}"
            for fa in frame_analyses
        )

        # 如果有音频信息，加入 prompt
        audio_section = ""
        if audio_info:
            audio_section = f"\n\n## 音频信息\n{audio_info}\n"

        prompt = SYNTHESIS_PROMPT.format(
            frame_count=len(frame_analyses),
            video_title=metadata.get("title", ""),
            descriptions=descriptions + audio_section,
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
                progress_callback=None) -> dict:
        """完整视频分析流程.

        Returns:
            {frame_analysis: [...], synthesis: str, audio_info: str}
        """
        session_id = uuid.uuid4().hex[:8]
        work_dir = Path("data") / "frames" / session_id
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: 抽帧 + 提取音频（并行）
            logger.info("Step 1/4: 抽取关键帧 + 音频...")
            frame_paths = self.extract_frames(video_path, str(work_dir))
            audio_path = self.extract_audio(video_path, str(work_dir))

            # 分析音频
            audio_info = ""
            if audio_path:
                audio_info = _transcribe_audio(audio_path)
                if progress_callback:
                    progress_callback("音频已提取: " + audio_info.split(";")[0])

            # Step 2: 逐帧分析
            logger.info(f"Step 2/4: AI 分析 {len(frame_paths)} 帧...")
            if progress_callback:
                progress_callback(f"正在分析 {len(frame_paths)} 个关键帧（约需 30-60 秒）...")
            frame_analyses = self.analyze_all_frames(frame_paths)

            # Step 3: 综合理解
            logger.info("Step 3/4: AI 综合理解...")
            if progress_callback:
                progress_callback("正在生成综合理解...")
            synthesis = self.synthesize_video(frame_analyses, {
                "title": title, "author": author,
            }, audio_info)

            # Step 4: 清理
            logger.info("Step 4/4: 清理临时文件...")

            return {
                "frame_analysis": frame_analyses,
                "synthesis": synthesis,
                "audio_info": audio_info,
            }
        finally:
            import shutil
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass
