"""
抖音视频提取：解析分享链接 → 获取下载 URL → 下载视频。
"""
import re
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Optional

import requests

from config import IPHONE_UA, FFMPEG_PATH, RETRY_MAX, RETRY_BACKOFF, \
    MAX_VIDEO_DURATION_SEC, MIN_FREE_DISK_BYTES, \
    HTTP_TIMEOUT_SHORT, HTTP_TIMEOUT_LONG, SUBPROCESS_TIMEOUT_FFMPEG_DOWNLOAD

logger = logging.getLogger(__name__)


class DouyinError(Exception):
    """抖音提取错误."""
    pass


class DouyinExtractor:
    """抖音视频提取器."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": IPHONE_UA})

    # ============================================================
    # URL 提取（从分享文本中）
    # ============================================================

    @staticmethod
    def extract_url_from_text(text: str) -> Optional[str]:
        """从任意文本中提取第一个抖音视频链接.

        用户可能直接粘贴抖音分享文本, 如:
        "【视频】https://v.douyin.com/abc123/ 复制打开抖音，看看他的作品"

        支持格式:
        - https://v.douyin.com/xxxxx/
        - https://www.douyin.com/video/1234567890123456789
        - https://www.iesdouyin.com/share/video/1234567890123456789
        - https://douyin.com/video/1234567890123456789
        """
        patterns = [
            r'https?://v\.douyin\.com/[a-zA-Z0-9_\-]+/?',
            r'https?://www\.douyin\.com/video/\d{15,25}',
            r'https?://www\.iesdouyin\.com/share/video/\d{15,25}',
            r'https?://douyin\.com/video/\d{15,25}',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0).rstrip('/')
        return None

    # ============================================================
    # 视频 ID 解析
    # ============================================================

    @staticmethod
    def extract_video_id(url: str) -> str:
        """从多种抖音 URL 格式中解析视频 ID.

        支持格式:
        - https://www.douyin.com/video/7645623388727217435
        - https://v.douyin.com/xxxxx/ (短链接)
        - https://www.iesdouyin.com/share/video/7645623388727217435
        - 纯数字 19 位 ID
        """
        url = url.strip()

        # 短链接: 先尝试跟踪重定向
        if "v.douyin.com" in url:
            for attempt in range(RETRY_MAX):
                try:
                    resp = requests.head(url, allow_redirects=True,
                                         timeout=HTTP_TIMEOUT_SHORT,
                                         headers={"User-Agent": IPHONE_UA})
                    url = resp.url
                    logger.info("短链接已跟踪: %s", url[:80])
                    break
                except Exception as e:
                    if attempt == RETRY_MAX - 1:
                        logger.warning("短链接重定向失败（已重试%d次）: %s，尝试直接解析", RETRY_MAX, e)
                    else:
                        time.sleep(RETRY_BACKOFF * (2 ** attempt))

        # 模式 1: /video/1234567890123456789
        match = re.search(r'/video/(\d{15,25})', url)
        if match:
            return match.group(1)

        # 模式 2: /share/video/1234567890123456789
        match = re.search(r'/share/video/(\d{15,25})', url)
        if match:
            return match.group(1)

        # 模式 3: modal_id, video_id query params
        for param in ["modal_id", "video_id", "item_id"]:
            match = re.search(rf'[?&]{param}=(\d{{15,25}})', url)
            if match:
                return match.group(1)

        # 模式 4: 纯数字 ID
        if re.match(r'^\d{15,25}$', url):
            return url

        raise DouyinError(f"无法从链接中解析视频 ID: {url}")

    # ============================================================
    # 视频信息获取
    # ============================================================

    def fetch_video_info(self, video_id: str) -> dict:
        """从抖音分享页获取视频元数据和下载 URL.

        Returns:
            {"video_url": str, "title": str, "author": str, "cover": str, "video_id": str}
        """
        share_url = f"https://www.iesdouyin.com/share/video/{video_id}"
        logger.info(f"请求抖音分享页: {share_url}")

        for attempt in range(RETRY_MAX):
            try:
                resp = self._session.get(share_url, timeout=HTTP_TIMEOUT_LONG)
                break
            except requests.RequestException as e:
                if attempt == RETRY_MAX - 1:
                    raise DouyinError(f"访问抖音页面失败: {e}")
                time.sleep(RETRY_BACKOFF * (2 ** attempt))

        html = resp.text

        # 提取 window._ROUTER_DATA
        match = re.search(
            r'window\._ROUTER_DATA\s*=\s*(\{.*\});?\s*</script>',
            html, re.DOTALL
        )
        if not match:
            raise DouyinError(
                "无法从页面中提取视频数据。可能原因:\n"
                "1. 视频链接已失效或为私密视频\n"
                "2. 抖音页面结构已更新，请联系开发者"
            )

        try:
            raw_json = match.group(1)
            # 如果贪婪匹配抓到太多内容，向后收缩到最后一个完整 JSON
            stack = 0
            last_valid = 0
            for i, c in enumerate(raw_json):
                if c == '{':
                    stack += 1
                elif c == '}':
                    stack -= 1
                    if stack == 0:
                        last_valid = i + 1
                        break
            if last_valid > 0:
                raw_json = raw_json[:last_valid]
            router_data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise DouyinError(f"页面数据解析失败: {e}")

        # 导航到视频信息
        # 抖音页面结构可能变化：尝试多种 key 模式
        # - 旧版: video_{video_id}/page
        # - 新版: video_(id)/page (字面字符串)
        loader_data = router_data.get("loaderData", {})

        # 先尝试精确匹配
        video_key = f"video_{video_id}/page"
        page_data = loader_data.get(video_key)

        # 如果没找到，遍历 loaderData 找包含 "video" 和 "/page" 的 key
        if page_data is None:
            for key in loader_data:
                if "video" in key.lower() and "/page" in key:
                    page_data = loader_data[key]
                    logger.info(f"使用匹配的 loader key: {key}")
                    break

        if page_data is None:
            raise DouyinError(
                "无法从页面数据中定位视频信息。可能原因:\n"
                "1. 视频链接已失效或为私密视频\n"
                "2. 抖音页面结构已更新，请联系开发者\n"
                f"可用的 loader keys: {list(loader_data.keys())}"
            )

        video_info_res = page_data.get("videoInfoRes", {})

        # 提取 item_list
        item_list = video_info_res.get("item_list", [])
        if not item_list:
            raise DouyinError("视频不存在或已被删除")

        video = item_list[0].get("video", {})
        play_addr = video.get("play_addr", {})
        url_list = play_addr.get("url_list", [])

        if not url_list:
            raise DouyinError("未找到视频下载地址")

        # 去水印：尝试多种已知模式
        video_url = url_list[0]
        for wm, clean in [("playwm", "play"), ("-wm", ""), ("_wm", "")]:
            if wm in video_url:
                video_url = video_url.replace(wm, clean)
                break
        if "playwm" in url_list[0] and "playwm" not in video_url:
            logger.info("水印已处理: %s → %s", url_list[0][:80], video_url[:80])
        elif "playwm" in url_list[0]:
            logger.warning("水印替换可能无效，URL 仍含 playwm: %s", video_url[:80])

        desc = item_list[0].get("desc", "Untitled")
        author_info = item_list[0].get("author", {})
        author_name = author_info.get("nickname", "Unknown")
        cover = video.get("cover", {}).get("url_list", [""])[0]

        return {
            "video_url": video_url,
            "title": desc,
            "author": author_name,
            "cover": cover,
            "video_id": video_id,
        }

    # ============================================================
    # 视频下载
    # ============================================================

    def download_video(self, video_url: str, output_path: str) -> Path:
        """下载视频到本地.

        Args:
            video_url: 视频直链
            output_path: 输出目录或文件路径

        Returns:
            Path: 下载后的文件路径
        """
        import shutil

        output = Path(output_path)
        if output.is_dir():
            output = output / "video.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)

        # 检查磁盘剩余空间
        free_bytes = shutil.disk_usage(output.parent).free
        if free_bytes < MIN_FREE_DISK_BYTES:
            raise DouyinError(
                f"服务器磁盘空间不足（剩余 {free_bytes / 1024 / 1024:.0f} MB），"
                f"请稍后再试。"
            )
        logger.info(f"磁盘剩余: {free_bytes / 1024 / 1024:.0f} MB")

        logger.info(f"下载视频: {video_url[:80]}...")
        logger.info(f"保存到: {output}")

        # 使用 FFmpeg 下载（自动处理重定向，限制时长），带重试
        cmd = [
            FFMPEG_PATH, "-y",
            "-user_agent", IPHONE_UA,
            "-t", str(MAX_VIDEO_DURATION_SEC),  # 限制最大时长
            "-i", video_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(output),
        ]
        for attempt in range(RETRY_MAX):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        timeout=SUBPROCESS_TIMEOUT_FFMPEG_DOWNLOAD)
                if result.returncode == 0 and output.exists() and output.stat().st_size > 1000:
                    size_mb = output.stat().st_size / 1024 / 1024
                    logger.info(f"FFmpeg 下载成功: {size_mb:.1f}MB")
                    # 下载后检查文件大小：超过剩余空间一半时警告
                    if size_mb > free_bytes * 0.5 / (1024 * 1024):
                        logger.warning("视频较大 (%dMB)，磁盘空间紧张，但仍继续处理", int(size_mb))
                    return output
                # 失败：清理部分文件，准备重试
                if output.exists():
                    output.unlink(missing_ok=True)
                logger.warning("FFmpeg 下载失败 (attempt %d/%d): %s",
                               attempt + 1, RETRY_MAX, result.stderr[:200])
            except subprocess.TimeoutExpired:
                if output.exists():
                    output.unlink(missing_ok=True)
                logger.warning("FFmpeg 超时 (attempt %d/%d)", attempt + 1, RETRY_MAX)
            except FileNotFoundError:
                raise DouyinError("FFmpeg 不可用，请确保已安装 FFmpeg")

            if attempt < RETRY_MAX - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))

        raise DouyinError(f"FFmpeg 下载失败（已重试 {RETRY_MAX} 次）")

    # ============================================================
    # 完整流程
    # ============================================================

    def extract(self, url: str, output_dir: str) -> dict:
        """完整提取流程: 预处理输入 → 解析链接 → 获取信息 → 下载视频.

        输入可以是:
        - 干净的抖音链接（如 https://v.douyin.com/xxx/）
        - 包含链接的分享文本（如 "【视频】https://v.douyin.com/xxx/ 复制打开抖音..."）

        Returns:
            {"video_path": str, "title": str, "author": str, "video_id": str}
        """
        # 预处理：如果输入不是以 http 开头的干净 URL，尝试从中提取 URL
        if not url.startswith("http"):
            extracted = self.extract_url_from_text(url)
            if extracted:
                logger.info(f"从分享文本中提取到链接: {extracted}")
                url = extracted
            else:
                raise DouyinError(
                    "未在输入中找到有效的抖音视频链接。\n"
                    "请直接粘贴抖音链接，或粘贴包含链接的分享文本（如：复制打开抖音后看到的文本）。"
                )

        video_id = self.extract_video_id(url)
        logger.info(f"视频 ID: {video_id}")

        info = self.fetch_video_info(video_id)
        logger.info(f"标题: {info['title']}")
        logger.info(f"作者: {info['author']}")

        video_path = self.download_video(info["video_url"], output_dir)

        return {
            "video_path": str(video_path),
            "title": info["title"],
            "author": info["author"],
            "video_id": video_id,
        }
