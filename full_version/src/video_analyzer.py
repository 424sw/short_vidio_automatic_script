"""
AI 视频分析：FFmpeg 抽帧 + 音频转录 → agnes-2.0-flash 分析 → 综合理解。
"""
import os
import re
import json
import base64
import logging
import subprocess
import uuid
import time
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

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
    return OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY, timeout=120.0)


# ============================================================
# 批量帧分析：所有帧一次 API 调用（不再逐帧请求）
# ============================================================

# 单次 API 最多塞多少帧（按图片大小 ~100KB 估算，20 帧约 2MB，安全）
_BATCH_SIZE = 20


def _analyze_frames_batch(frame_paths: list[str], quality: str = "standard") -> list[dict]:
    """一次性分析多张帧——所有图片放入单次 API 调用。

    比逐帧调用快 N-1 倍（N = 帧数），且无并发线程开销。
    """
    if not frame_paths:
        return []

    client = _make_client()
    detail_level = get_quality_config(quality).get("vision_detail", "详细描述画面内容")

    # 构建多图消息
    content_blocks = []
    name_list = []
    for fp in frame_paths:
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
        name_list.append(Path(fp).name)

    frame_list_str = "\n".join(f"{i+1}. {name}" for i, name in enumerate(name_list))
    text_prompt = (
        f"请分析以下 {len(frame_paths)} 张视频关键帧。{detail_level}。\n\n"
        f"帧列表：\n{frame_list_str}\n\n"
        f"请逐一描述每帧，格式如下（用空行分隔）：\n"
        f"--- 帧 {name_list[0]}\n画面描述...\n"
        f"--- 帧 {name_list[-1]}\n画面描述...\n"
        f"请用中文回复。"
    )
    content_blocks.append({"type": "text", "text": text_prompt})

    token_budget = min(600 * len(frame_paths), 8000)
    logger.info(f"批量分析 {len(frame_paths)} 帧（1 次 API 调用）...")

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=AGNES_MODEL,
                messages=[{"role": "user", "content": content_blocks}],
                max_tokens=token_budget,
                timeout=180,
            )
            raw = response.choices[0].message.content
            parsed = _parse_batch_result(raw, name_list)
            return parsed
        except Exception as e:
            logger.warning(f"批量帧分析 尝试 {attempt+1} 失败: {e}")
            if attempt == 1:
                return [
                    {"frame": name, "description": f"[分析失败: {e}]"}
                    for name in name_list
                ]
            time.sleep(2)

    return [{"frame": name, "description": "[分析失败]"} for name in name_list]


def _parse_batch_result(raw: str, name_list: list[str]) -> list[dict]:
    """解析批量 AI 返回，按 '--- 帧 xxx' 分隔符拆为逐帧描述."""
    if not raw:
        return [{"frame": name, "description": "[空响应]"} for name in name_list]

    # 按 --- 帧 xxx 分割
    parts = re.split(r'\n\s*---\s*帧\s*(.*?)\n', raw)
    results = []

    # parts[0] = AI 开场白; parts[1]=帧名, parts[2]=描述, parts[3]=帧名, ...
    for i in range(1, len(parts), 2):
        ref = parts[i].strip() if i < len(parts) else ""
        desc = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if not desc:
            continue
        # 匹配帧名
        matched = _match_frame_name(ref, name_list)
        if matched:
            results.append({"frame": matched, "description": desc})

    # 补充缺失的帧
    found = {r["frame"] for r in results}
    for name in name_list:
        if name not in found:
            # 尝试从原始文本直接搜索帧名
            escaped = re.escape(name)
            m = re.search(rf'{escaped}.*?\n(.*?)(?=\n---|\n$|\Z)', raw, re.DOTALL)
            desc = m.group(1).strip()[:500] if m else "[响应中未找到此帧]"
            results.append({"frame": name, "description": desc})

    # 按原始顺序排序
    order = {n: i for i, n in enumerate(name_list)}
    results.sort(key=lambda r: order.get(r["frame"], 999))
    return results


def _match_frame_name(ref: str, name_list: list[str]) -> str | None:
    """模糊匹配帧名."""
    ref_clean = ref.strip()
    for name in name_list:
        if name == ref_clean or name in ref_clean or ref_clean in name:
            return name
    # 尝试数字匹配
    m = re.search(r'(\d+)', ref_clean)
    if m:
        num = m.group(1)
        for name in name_list:
            if num in name:
                return name
    return None


# ============================================================
# 音频转录（faster-whisper）
# ============================================================

# 项目本地模型目录（运行 setup_models.py 下载一次后存在）
_LOCAL_MODEL_DIR = Path(__file__).parent.parent / "models" / "faster-whisper-tiny"


def _get_whisper_model_path() -> tuple[str, bool]:
    """返回 (model_size_or_path, local_files_only)。"""
    if _LOCAL_MODEL_DIR.exists() and (_LOCAL_MODEL_DIR / "model.bin").exists():
        return str(_LOCAL_MODEL_DIR), True
    return "tiny", False


def _transcribe_audio(audio_path: str) -> str:
    """使用 faster-whisper 将音频转为文字。

    优先使用项目本地模型，不存在时自动从 HuggingFace 在线下载。
    """
    p = Path(audio_path)
    if not p.exists() or p.stat().st_size < 1000:
        return ""

    duration_sec = _get_audio_duration(audio_path)
    if duration_sec < 5:
        logger.info(f"音频过短（{duration_sec:.1f}秒），跳过转录")
        return ""

    model_path, local_only = _get_whisper_model_path()
    source = "本地模型" if local_only else "在线下载"
    logger.info(f"正在转录音频（{source}，时长 {duration_sec:.1f} 秒）...")

    import threading

    result_holder = []

    def _run_transcribe():
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                model_path, device="cpu", compute_type="int8",
                local_files_only=local_only,
            )
            segments, info = model.transcribe(
                audio_path, language="zh", beam_size=5, vad_filter=True,
            )
            text_parts = [seg.text.strip() for seg in segments]
            full_text = "".join(text_parts)
            if full_text:
                result_holder.append(
                    f"音频时长: {duration_sec:.1f}秒\n音频转录文字:\n{full_text}\n检测语言: {info.language}"
                )
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"音频转录出错: {e}")

    thread = threading.Thread(target=_run_transcribe, daemon=True)
    thread.start()
    thread.join(timeout=120)

    if result_holder:
        logger.info(f"音频转录成功: {len(result_holder[0])} 字符")
        return result_holder[0]

    if thread.is_alive():
        logger.warning("音频转录超时（120秒），跳过，继续分析")

    audio_codec = _get_audio_codec(audio_path)
    return f"音频时长: {duration_sec:.1f}秒, 编码: {audio_codec}（未转录文字）"


def _get_audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）."""
    cmd = [FFMPEG_PATH, "-i", str(audio_path), "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def _get_audio_codec(audio_path: str) -> str:
    """获取音频编码格式."""
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
        pass

    # ============================================================
    # FFmpeg 抽帧 & 音频提取
    # ============================================================

    def extract_frames(self, video_path: str, output_dir: str,
                       quality: str = "standard") -> list[Path]:
        """从视频中抽取关键帧。

        使用 fps + mpdecimate 去重，保留画面变化处，跨平台兼容。
        """
        preset = get_quality_config(quality)
        max_frames = preset["max_frames"]

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        for old in output.glob("frame_*.jpg"):
            old.unlink()
        for d in ("scenes", "uniform"):
            for old in (output / d).glob("*") if (output / d).exists() else []:
                old.unlink()

        # 统一：fps=2 采样 + mpdecimate 去除相邻相似帧
        fps = 2  # 每秒 2 帧够捕捉所有切换
        logger.info(f"FFmpeg 抽帧 (fps={fps} + mpdecimate): {video_path}")
        cmd = [
            FFMPEG_PATH, "-i", str(video_path),
            "-vf", f"fps={fps},mpdecimate=hi=64:lo=64:frac=1",
            "-vsync", "vfr",
            "-q:v", "2",
            str(output / "frame_%03d.jpg"),
            "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            # mpdecimate 可能失败，回退到纯 fps 采样
            logger.warning(f"mpdecimate 失败，回退到纯 fps 采样: {result.stderr[:200]}")
            result = subprocess.run([
                FFMPEG_PATH, "-i", str(video_path),
                "-vf", f"fps={fps}",
                "-q:v", "2",
                str(output / "frame_%03d.jpg"),
                "-y",
            ], capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                raise VideoAnalysisError(f"FFmpeg 抽帧失败: {result.stderr[:300]}")

        frames = sorted(output.glob("frame_*.jpg"))
        if not frames:
            raise VideoAnalysisError("未抽到任何帧。视频可能太短（< 5 秒）。")

        # 均匀采样到上限
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
            "-vn", "-acodec", "libmp3lame",
            "-ar", "16000", "-ac", "1", "-q:a", "5",
            str(audio_path), "-y",
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
        """分析所有帧——全部帧放入单个 API 调用，一次返回所有描述。

        不再逐帧调用 API，不再使用线程池。
        20 帧以内：1 次 API 调用；超过 20 帧：分多批，每批 1 次调用。
        """
        frame_str_paths = [str(fp) for fp in frame_paths]
        total = len(frame_str_paths)

        if total <= _BATCH_SIZE:
            logger.info(f"分析 {total} 帧 → 1 次 API 调用")
            return _analyze_frames_batch(frame_str_paths, quality)

        # 帧数过多时分批（罕见，场景检测后通常不会超过 20 帧）
        all_results = []
        for start in range(0, total, _BATCH_SIZE):
            batch = frame_str_paths[start:start + _BATCH_SIZE]
            logger.info(f"分析批次 {start//_BATCH_SIZE + 1}: {len(batch)} 帧")
            all_results.extend(_analyze_frames_batch(batch, quality))

        return all_results

    def synthesize_video(self, frame_analyses: list[dict],
                         metadata: dict, audio_transcript: str = "") -> str:
        """汇总逐帧描述 + 音频转录，生成综合视频理解."""
        logger.info("AI 综合理解...")

        descriptions = "\n\n---\n\n".join(
            f"【{fa['frame']}】\n{fa['description']}"
            for fa in frame_analyses
        )

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

        Returns:
            {frame_analysis: [...], synthesis: str, audio_transcript: str, quality: str}
        """
        preset = get_quality_config(quality)
        session_id = uuid.uuid4().hex[:8]
        work_dir = Path("data") / "frames" / session_id
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: 抽帧 + 抽取音频（并行）
            logger.info(f"Step 1/4: 抽取关键帧 + 音频提取 ({preset['label']}模式)...")

            with ThreadPoolExecutor(max_workers=2) as ex:
                fut_frames = ex.submit(
                    self.extract_frames, video_path, str(work_dir), quality)
                fut_audio = ex.submit(
                    self.extract_audio, video_path, str(work_dir))
                frame_paths = fut_frames.result(timeout=180)
                audio_path = fut_audio.result(timeout=120)

            # 转录音频（与帧分析并行）
            audio_transcript = ""
            if audio_path:
                if progress_callback:
                    progress_callback("正在进行语音转文字...")
                # 在后台线程跑转录，主线程同时做帧分析
                import threading
                transcribe_result = []

                def _bg_transcribe():
                    transcribe_result.append(_transcribe_audio(audio_path))

                t = threading.Thread(target=_bg_transcribe, daemon=True)
                t.start()
            else:
                t = None

            # Step 2: 逐帧分析（全部帧一次 API 调用）
            logger.info(f"Step 2/4: AI 分析 {len(frame_paths)} 帧...")
            if progress_callback:
                progress_callback(
                    f"正在 AI 分析 {len(frame_paths)} 个关键帧"
                    f"（{preset['label']}模式，{preset['est_time']}）..."
                )
            frame_analyses = self.analyze_all_frames(frame_paths, quality)

            # 等待转录完成
            if t is not None:
                t.join(timeout=30)
                if transcribe_result:
                    audio_transcript = transcribe_result[0]
                    if progress_callback and audio_transcript:
                        preview = audio_transcript.split("\n")[-1][:80] if "\n" in audio_transcript else audio_transcript[:80]
                        progress_callback(f"语音识别完成: {preview}...")

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
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass
