"""
磁盘 Checkpoint 持久化 — 应对 WebSocket 断开导致的会话丢失。

在 ModelScope 免费层上，Streamlit 会话可能在浏览器切后台时被回收。
此模块将管道中间状态持久化到磁盘，支持断点续跑。

同时提供过期会话和下载文件的定期清理。
"""
import json
import hashlib
import time
import shutil
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path("data") / "sessions"
DOWNLOADS_DIR = Path("data") / "downloads"
FRAMES_DIR = Path("data") / "frames"

MAX_SESSION_AGE_HOURS = 24
MAX_DOWNLOAD_AGE_HOURS = 1


def _compute_hash(video_url: str) -> str:
    """对视频 URL 取 SHA256 前 16 位作为 session key."""
    return hashlib.sha256(video_url.strip().encode()).hexdigest()[:16]


def get_session_dir(video_url: str) -> Path:
    """获取与视频 URL 绑定的持久化 session 目录."""
    session_hash = _compute_hash(video_url)
    session_dir = SESSIONS_DIR / session_hash
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# ============================================================
# Checkpoint 保存 / 加载
# ============================================================


def save_checkpoint(video_url: str, state: dict) -> bool:
    """将管道状态保存到磁盘.

    Args:
        video_url: 视频 URL（作为 session 标识）
        state: 需要持久化的状态字典

    Returns:
        True 表示保存成功
    """
    try:
        session_dir = get_session_dir(video_url)
        state["_saved_at"] = time.time()
        state["_video_hash"] = _compute_hash(video_url)

        checkpoint_file = session_dir / "state.json"
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.info(f"Checkpoint 已保存: step={state.get('step')}")
        return True
    except Exception as e:
        logger.warning(f"Checkpoint 保存失败: {e}")
        return False


def load_checkpoint(video_url: str) -> Optional[dict]:
    """尝试从磁盘恢复之前的会话状态.

    Args:
        video_url: 视频 URL

    Returns:
        恢复的状态字典，如果不存在或已过期则返回 None
    """
    session_hash = _compute_hash(video_url)
    checkpoint_file = SESSIONS_DIR / session_hash / "state.json"

    if not checkpoint_file.exists():
        return None

    try:
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        age_hours = (time.time() - state.get("_saved_at", 0)) / 3600
        if age_hours > MAX_SESSION_AGE_HOURS:
            logger.info(f"Checkpoint 已过期（{age_hours:.1f}h），忽略")
            _cleanup_session(session_hash)
            return None

        logger.info(f"Checkpoint 已加载: step={state.get('step')}, age={age_hours:.1f}h")
        return state
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Checkpoint 损坏: {e}")
        _cleanup_session(session_hash)
        return None


# ============================================================
# 清理
# ============================================================


def cleanup_old_sessions():
    """清理过期的会话数据和下载文件."""
    now = time.time()

    # 清理过期 session
    if SESSIONS_DIR.exists():
        for session_dir in list(SESSIONS_DIR.iterdir()):
            if not session_dir.is_dir():
                continue
            checkpoint = session_dir / "state.json"
            if checkpoint.exists():
                try:
                    with open(checkpoint) as f:
                        state = json.load(f)
                    age = now - state.get("_saved_at", 0)
                    if age > MAX_SESSION_AGE_HOURS * 3600:
                        _cleanup_session(session_dir.name)
                except Exception:
                    _cleanup_session(session_dir.name)
            else:
                mtime = session_dir.stat().st_mtime
                if now - mtime > MAX_SESSION_AGE_HOURS * 3600:
                    _cleanup_session(session_dir.name)

    # 清理旧下载文件
    if DOWNLOADS_DIR.exists():
        for subdir in list(DOWNLOADS_DIR.iterdir()):
            if not subdir.is_dir():
                continue
            mtime = subdir.stat().st_mtime
            if now - mtime > MAX_DOWNLOAD_AGE_HOURS * 3600:
                shutil.rmtree(subdir, ignore_errors=True)
                logger.info(f"已清理旧下载目录: {subdir.name}")

    # 清理残留帧目录
    if FRAMES_DIR.exists():
        for subdir in list(FRAMES_DIR.iterdir()):
            if not subdir.is_dir():
                continue
            mtime = subdir.stat().st_mtime
            if now - mtime > MAX_DOWNLOAD_AGE_HOURS * 3600:
                shutil.rmtree(subdir, ignore_errors=True)
                logger.info(f"已清理残留帧目录: {subdir.name}")


def _cleanup_session(session_hash: str):
    """删除指定 session 目录."""
    session_dir = SESSIONS_DIR / session_hash
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
        logger.info(f"已清理 session: {session_hash}")


def delete_checkpoint(video_url: str):
    """完成时删除 checkpoint（不保留已完成任务的恢复点）."""
    session_hash = _compute_hash(video_url)
    _cleanup_session(session_hash)

