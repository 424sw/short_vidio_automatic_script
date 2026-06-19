"""图片匹配器：实时扫描表情包库 → 关键词匹配 → 并行下载。

每次会话启动时扫描一次库 blocks（~1-2 次 API 调用，瞬时完成），
在内存中建立索引，永不过期。不产生任何静态文件。
"""

import re
import logging
import requests

from config import (
    FEISHU_BASE_URL, EMOJI_LIBRARY_DOC_ID,
    HTTP_TIMEOUT_MEDIUM,
)

logger = logging.getLogger(__name__)

# 递归遍历时需要深入这些容器类型
_CONTAINER_TYPES = {
    1,                          # Page
    10, 11, 12, 13, 14, 15,    # 列表 (Bullet/Ordered 等)
    19,                         # Callout
    22, 23, 24,                 # Grid / Column
    32,                         # TableCell
    31,                         # Table（它的 children 是 cell，需要进去拿 cell 的 children）
}
HEADING_TYPES = {3, 4, 5, 6, 7, 8, 9}
IMAGE_TYPE = 27
TEXT_TYPE = 2


class ImageMatcher:
    """实时扫描表情包库，匹配 + 下载。"""

    def __init__(self, feishu_client=None):
        self._client = feishu_client
        self._flat = []          # 全部可用图片（排除视频）
        self._by_category = {}   # 按分类索引
        self._scanned = False

    # ============================================================
    # 扫描库 — 递归遍历整棵 block 树
    # ============================================================

    def _ensure_scanned(self):
        if self._scanned:
            return
        self._scan_library()

    def _scan_library(self):
        logger.info("扫描表情包库: %s", EMOJI_LIBRARY_DOC_ID)

        blocks = self._fetch_all_blocks(EMOJI_LIBRARY_DOC_ID)
        block_map = {b["block_id"]: b for b in blocks}

        page = next((b for b in blocks if b["block_type"] == 1), None)
        if not page:
            logger.warning("表情包库找不到 page block，匹配器将返回空")
            self._scanned = True
            return

        seq = {}

        def walk(children_ids, current_category):
            """递归遍历子树，返回穿过所有子节点后的 category（供后续 sibling 使用）。"""
            if not children_ids:
                return current_category

            for bid in children_ids:
                b = block_map.get(bid)
                if not b:
                    continue
                bt = b["block_type"]

                # 分类标题 — 更新当前分类，同时递归其子节点
                if bt in HEADING_TYPES:
                    text = self._extract_text(b)
                    if text:
                        current_category = text
                    sub = b.get("children", [])
                    if sub:
                        current_category = walk(sub, current_category)
                    continue

                # 容器 — 递归进入
                if bt in _CONTAINER_TYPES:
                    sub = b.get("children", [])
                    if sub:
                        current_category = walk(sub, current_category)
                    continue

                # 图片 block
                if bt == IMAGE_TYPE:
                    img = b.get("image", {})
                    token = img.get("token", "")
                    if not token:
                        continue

                    cat = current_category
                    seq.setdefault(cat, 0)
                    seq[cat] += 1

                    # 从同层相邻文本 block 提取描述
                    name = self._neighbor_text(children_ids, bid, block_map)
                    if not name:
                        name = f"{cat}_{seq[cat]:03d}"

                    # 动态类 mp4 跳过
                    if "动态" in cat:
                        continue

                    entry = {
                        "name": name,
                        "token": token,
                        "category": cat,
                        "width": img.get("width", 0),
                        "height": img.get("height", 0),
                    }
                    self._by_category.setdefault(cat, []).append(entry)
                    self._flat.append(entry)

            return current_category

        walk(page.get("children", []), "未分类")

        img_count = len(self._flat)
        cat_count = len(self._by_category)
        logger.info("表情包库扫描完成: %d 张可用图片, %d 个分类", img_count, cat_count)
        if cat_count > 0:
            for cat, entries in sorted(self._by_category.items()):
                logger.info("  %s: %d 张", cat, len(entries))
        self._scanned = True

    @staticmethod
    def _neighbor_text(sibling_ids, target_bid, block_map):
        """在同层兄弟列表中查找目标块前后最近的文本 block。"""
        try:
            idx = sibling_ids.index(target_bid)
        except ValueError:
            return None
        for offset in [-1, 1]:
            ni = idx + offset
            if 0 <= ni < len(sibling_ids):
                nb = block_map.get(sibling_ids[ni])
                if nb and nb["block_type"] == TEXT_TYPE:
                    t = ImageMatcher._extract_text(nb)
                    if t and 2 <= len(t) <= 60:
                        return t
        return None

    def _fetch_all_blocks(self, doc_id):
        """分页获取文档全部 blocks。"""
        all_blocks = []
        page_token = None

        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token

            if self._client:
                data = self._client._request("GET",
                    f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks",
                    params=params)
            else:
                data = self._fetch_standalone(doc_id, params)

            items = data.get("data", {}).get("items", [])
            all_blocks.extend(items)

            if not data.get("data", {}).get("has_more"):
                break
            page_token = data["data"]["page_token"]

        return all_blocks

    def _fetch_standalone(self, doc_id, params):
        """独立请求（无 FeishuClient 时）。"""
        from config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_AUTH_URL
        resp = requests.post(FEISHU_AUTH_URL,
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=HTTP_TIMEOUT_MEDIUM)
        token = resp.json()["tenant_access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        r = requests.get(
            f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks",
            params=params, headers=headers, timeout=HTTP_TIMEOUT_MEDIUM)
        return r.json()

    @staticmethod
    def _extract_text(block):
        elements = block.get("text", {}).get("elements", [])
        return "".join(e.get("text_run", {}).get("content", "") for e in elements).strip()

    # ============================================================
    # 匹配
    # ============================================================

    def match(self, description: str) -> dict:
        """单条匹配，返回 {name, token, category, width, height} 或 None。"""
        self._ensure_scanned()
        if not self._flat:
            logger.warning("匹配器无可用图片，返回 None")
            return None

        keywords = self._extract_keywords(description)
        if not keywords:
            return self._random_universal() or self._random_any()

        scored = []
        for entry in self._flat:
            name = entry["name"]
            score = sum(1 for kw in keywords if kw in name)
            if score > 0:
                scored.append((score, entry))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0][1]
            logger.debug("匹配命中: '%s' → '%s' (score=%d)", description, best["name"], scored[0][0])
            return best

        fallback = self._random_universal() or self._random_any()
        logger.debug("匹配降级: '%s' → 兜底 '%s'", description, fallback["name"] if fallback else "None")
        return fallback

    def match_all(self, descriptions: list[str]) -> list[dict]:
        """批量匹配，返回与输入等长的结果列表（失败项为 None）。"""
        self._ensure_scanned()
        return [self.match(d) for d in descriptions]

    def _random_universal(self):
        pool = self._by_category.get("万能类", [])
        if pool:
            import random
            return random.choice(pool)
        return None

    def _random_any(self):
        if self._flat:
            import random
            return random.choice(self._flat)
        return None

    @staticmethod
    def _extract_keywords(description: str) -> list[str]:
        """从素材描述中提取中文关键词。"""
        if not description:
            return []

        cleaned = re.sub(r'\.[a-zA-Z0-9]+', '', description)
        cleaned = re.sub(r'[^一-鿿\w]', '', cleaned)

        keywords = []
        for n in [4, 3, 2]:
            if len(cleaned) >= n:
                for i in range(len(cleaned) - n + 1):
                    chunk = cleaned[i:i + n]
                    if re.match(r'^[一-鿿]{%d}$' % n, chunk):
                        keywords.append(chunk)

        stop = {"表情包", "素材", "图片", "配图", "插图"}
        keywords = [kw for kw in keywords if kw not in stop]
        return sorted(set(keywords), key=lambda x: -len(x))

    # ============================================================
    # 下载
    # ============================================================

    def download_one(self, token: str) -> bytes:
        """下载单张图片，返回 bytes。"""
        url = f"{FEISHU_BASE_URL}/drive/v1/medias/{token}/download"
        if self._client:
            self._client._ensure_token()
            headers = {"Authorization": self._client._session.headers.get("Authorization", "")}
        else:
            from config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_AUTH_URL
            resp = requests.post(FEISHU_AUTH_URL,
                json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
                timeout=HTTP_TIMEOUT_MEDIUM)
            token_str = resp.json()["tenant_access_token"]
            headers = {"Authorization": f"Bearer {token_str}"}

        r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_MEDIUM)
        if r.status_code != 200:
            logger.warning("下载图片失败: token=%s status=%d", token, r.status_code)
            return None
        return r.content

    def download_all(self, matches: list[dict]) -> list[dict]:
        """串行下载一批匹配结果中的图片（避免并行竞态 + 飞书限流）。"""
        results = []
        for m in matches:
            if not m:
                results.append({"image_bytes": None})
                continue
            try:
                img_bytes = self.download_one(m["token"])
                results.append({**m, "image_bytes": img_bytes})
            except Exception as e:
                logger.warning("下载图片异常: %s", e)
                results.append({**m, "image_bytes": None})
        return results
