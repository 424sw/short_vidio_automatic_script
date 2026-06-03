"""
飞书 API 客户端：认证、模板复制、权限设置、内容填充、图片操作。
"""
import time
import json
import uuid
import logging
import requests
from typing import Optional

from config import (
    FEISHU_APP_ID, FEISHU_APP_SECRET,
    FEISHU_AUTH_URL, FEISHU_BASE_URL,
    FOLDER_TOKEN, TEMPLATE_IDS,
    RETRY_MAX, RETRY_BACKOFF,
    generate_doc_title,
)

logger = logging.getLogger(__name__)


class FeishuError(Exception):
    """飞书 API 错误."""
    pass


class FeishuClient:
    """飞书 Open API 客户端."""

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ============================================================
    # 内部工具
    # ============================================================

    def _ensure_token(self):
        """确保 token 有效，必要时刷新."""
        if self._token and time.time() < self._token_expires_at:
            return

        logger.info("获取飞书 tenant_access_token...")
        resp = self._session.post(
            FEISHU_AUTH_URL,
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=15,
        )
        data = resp.json()
        if resp.status_code != 200 or "tenant_access_token" not in data:
            raise FeishuError(f"飞书认证失败: {data}")

        self._token = data["tenant_access_token"]
        expire_sec = data.get("expire", 7200)
        self._token_expires_at = time.time() + expire_sec - 300  # 提前5分钟刷新
        self._session.headers["Authorization"] = f"Bearer {self._token}"
        logger.info("飞书 token 获取成功")

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """带自动 token 刷新的 API 请求."""
        self._ensure_token()

        timeout = kwargs.pop("timeout", 30)
        for attempt in range(RETRY_MAX):
            resp = self._session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 401:
                # Token 可能提前过期，强制刷新后重试
                logger.warning("Token 过期，正在刷新...")
                self._token_expires_at = 0
                self._ensure_token()
                resp = self._session.request(method, url, timeout=timeout, **kwargs)

            # 先检查 HTTP 状态码
            if resp.status_code >= 400:
                content_type = resp.headers.get("content-type", "")
                if "json" not in content_type:
                    logger.error(f"飞书 API HTTP {resp.status_code}: {resp.text[:300]}")
                    raise FeishuError(
                        f"飞书 API 返回 HTTP {resp.status_code}（非 JSON 响应）。\n"
                        f"可能原因: 请求方法错误（如应使用PATCH而非POST）或 URL 路径错误。\n"
                        f"URL: {method} {url}"
                    )

            try:
                data = resp.json()
            except Exception as e:
                logger.error(f"JSON 解析失败: {resp.text[:300]}")
                raise FeishuError(
                    f"飞书 API 返回了非 JSON 内容 (HTTP {resp.status_code})。\n"
                    f"URL: {method} {url}\n"
                    f"响应前200字符: {resp.text[:200]}"
                )

            code = data.get("code", -1)
            if code == 0:
                return data
            if code == 99991400:  # rate limit
                wait = RETRY_BACKOFF * (2 ** attempt)
                logger.warning(f"飞书限流，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            # 详细错误日志
            logger.error(f"飞书 API 调用失败: {method} {url}")
            logger.error(f"响应: code={code}, msg={data.get('msg', 'unknown')}")
            raise FeishuError(f"飞书 API 错误 (code={code}): {data.get('msg', 'unknown')}")

        raise FeishuError("飞书 API 重试次数已用尽")

    # ============================================================
    # 模板操作
    # ============================================================

    def copy_template(self, template_type: str, seq: int = 1) -> dict:
        """复制模板，返回新文档的 doc_id 和 url.

        Args:
            template_type: "mix" 或 "oral"
            seq: 编号
        """
        template_id = TEMPLATE_IDS[template_type]
        name = generate_doc_title(template_type, seq)

        logger.info(f"复制{template_type}模板 → {name}")
        data = self._request(
            "POST",
            f"{FEISHU_BASE_URL}/drive/v1/files/{template_id}/copy",
            json={
                "name": name,
                "type": "docx",
                "folder_token": FOLDER_TOKEN,
            },
        )

        doc_id = data["data"]["file"]["token"]
        doc_url = data["data"]["file"]["url"]
        logger.info(f"副本创建成功: {doc_url}")
        return {"doc_id": doc_id, "url": doc_url}

    def set_public_permission(self, doc_id: str) -> bool:
        """设置文档为互联网任何人可编辑."""
        logger.info(f"设置公开权限: {doc_id}")
        data = self._request(
            "PATCH",
            f"{FEISHU_BASE_URL}/drive/v1/permissions/{doc_id}/public",
            params={"type": "docx"},
            json={
                "link_share_entity": "anyone_editable",
                "external_access": True,
                "invite_external": True,
            },
        )
        return data.get("code") == 0

    # ============================================================
    # Block 操作
    # ============================================================

    def get_blocks(self, doc_id: str) -> list[dict]:
        """获取文档所有 blocks."""
        all_blocks = []
        page_token = None

        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks",
                params=params,
            )

            items = data.get("data", {}).get("items", [])
            all_blocks.extend(items)

            if not data.get("data", {}).get("has_more"):
                break
            page_token = data["data"]["page_token"]

        return all_blocks

    def update_text_block(self, doc_id: str, block_id: str, content: str,
                          bold: bool = False, background_color: int = None) -> dict:
        """更新文本 block 的内容."""
        style = {
            "bold": bold,
            "inline_code": False,
            "italic": False,
            "strikethrough": False,
            "underline": False,
        }
        if background_color is not None:
            style["background_color"] = background_color

        body = {
            "update_text_elements": {
                "elements": [{
                    "text_run": {
                        "content": content,
                        "text_element_style": style,
                    }
                }]
            }
        }

        return self._request(
            "PATCH",
            f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block_id}",
            json=body,
        )

    def insert_table_row(self, doc_id: str, table_block_id: str, row_index: int) -> dict:
        """向表格插入一行."""
        return self._request(
            "PATCH",
            f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/batch_update",
            json={
                "requests": [{
                    "block_id": table_block_id,
                    "insert_table_row": {"row_index": row_index},
                }]
            },
        )

    def update_sheet_title(self, sheet_token: str, title: str) -> dict:
        """将标题写入嵌入表格的 A2 单元格."""
        # 去掉 _gWovo0 后缀得到 spreadsheetToken
        spreadsheet_token = sheet_token.replace("_gWovo0", "")

        return self._request(
            "PUT",
            f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values",
            json={
                "valueRange": {
                    "range": "gWovo0!A2:B2",
                    "values": [[title, "否"]],
                }
            },
        )

    # ============================================================
    # 图片操作
    # ============================================================
    # 注意：飞书 docx API（tenant_access_token 模式）当前不支持创建/修改
    # Image Block (block_type=27)。所有尝试（descendant、children、
    # batch_update replace_image/add_blocks、inline_file）均返回 1770001
    # 或静默忽略。目前仅支持图片上传，文档中仍用文字描述替代图片。
    #
    # 如将来飞书开放此能力，可取消下方注释并测试 insert_image_block。

    def upload_image_media(self, image_path: str, parent_node: str) -> str:
        """上传图片素材到飞书，返回 file_token。

        **必须传入 parent_node**（文档 block_id），否则返回 403。

        Args:
            image_path: 本地图片文件路径
            parent_node: 文档 page block_id 或目标 block_id

        Returns:
            file_token: 图片的 file_token
        """
        from pathlib import Path

        p = Path(image_path)
        if not p.exists():
            raise FeishuError(f"图片文件不存在: {image_path}")

        file_size = p.stat().st_size
        file_name = p.name
        logger.info(f"上传图片: {file_name} ({file_size / 1024:.1f}KB)")

        self._ensure_token()
        url = f"{FEISHU_BASE_URL}/drive/v1/medias/upload_all"

        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                data={
                    "file_name": file_name,
                    "parent_type": "docx_image",
                    "parent_node": parent_node,
                    "size": str(file_size),
                },
                files={"file": (file_name, f.read(), "image/png")},
                timeout=60,
            )

        if resp.status_code != 200:
            raise FeishuError(f"图片上传失败 (HTTP {resp.status_code}): {resp.text[:500]}")

        try:
            data = resp.json()
        except Exception:
            raise FeishuError(f"图片上传返回非 JSON: {resp.text[:300]}")

        code = data.get("code", -1)
        if code != 0:
            raise FeishuError(
                f"图片上传 API 错误 (code={code}): {data.get('msg', 'unknown')}"
            )

        file_token = data.get("data", {}).get("file_token", "")
        logger.info(f"图片上传成功: file_token={file_token}")
        return file_token

    # ---- 以下方法因飞书 API 限制暂不可用 ----

    # def insert_image_block(self, doc_id: str, parent_block_id: str,
    #                        image_token: str, index: int = 0) -> dict:
    #     """在文档中插入图片 block（descendant API）。
    #     注意：当前飞书 docx API 不支持创建 Image Block，此方法暂不可用。
    #     """
    #     ...

    # def insert_image_into_table_cell(self, ...) -> bool:
    #     """在表格单元格中插入图片。
    #     注意：依赖 insert_image_block，当前不可用。
    #     """
    #     ...

    # ============================================================
    # 模板填充编排
    # ============================================================

    def fill_mix_script(self, doc_id: str, script: dict, video_url: str, video_title: str) -> str:
        """填充混剪模板.

        混剪模板结构（按 children 顺序）:
          [0] "视频形式参考视频" (type=2)
          [1] Callout (type=19) → 内嵌文本块（填入视频链接）
          [2] "标题（剪辑不用管）" (type=2)
          [3] 空文本块 (type=2) — 填入脚本标题
          [4] "图文配置" (type=2)
          [5] 表格 (type=31, 2列, 10行)
          [6+] 交付要求 (headings, bullets)
        """
        blocks = self.get_blocks(doc_id)
        block_map = {b["block_id"]: b for b in blocks}

        # 找到 page block
        page = next((b for b in blocks if b["block_type"] == 1), None)
        if not page:
            raise FeishuError("找不到 page block")
        children_ids = page.get("children", [])

        # 获取 page 的直接子 block（按顺序）
        page_children = [block_map[cid] for cid in children_ids if cid in block_map]

        # --- Step 1: 页面标题（跳过，page block 不支持 update_text_elements） ---
        # 模板复制时已通过 name 参数设置了文档标题，无需额外更新

        # --- Step 2: 更新参考视频链接 ---
        # 找到 callout (type=19) 内部的文本块
        callout = next((b for b in page_children if b["block_type"] == 19), None)
        if callout:
            callout_child_ids = callout.get("children", [])
            if callout_child_ids:
                link_block_id = callout_child_ids[0]
                self.update_text_block(doc_id, link_block_id, f"参考视频：{video_url}")

        # --- Step 3: 填入脚本标题 ---
        # 在 page_children 中按位置找到: 第4个是空文本块（index=3）
        # 即 "标题（剪辑不用管）" 文本块之后、 "图文配置" 之前的空块
        title_block = None
        for i, child in enumerate(page_children):
            if child["block_type"] == 2:
                text_elements = child.get("text", {}).get("elements", [])
                content = "".join(e.get("text_run", {}).get("content", "") for e in text_elements)
                if content == "" and i > 2:  # 跳过前面的空文本
                    # 确认前一个文本块是"标题（剪辑不用管）"
                    prev = page_children[i-1] if i > 0 else None
                    if prev and prev["block_type"] == 2:
                        prev_content = "".join(
                            e.get("text_run", {}).get("content", "")
                            for e in prev.get("text", {}).get("elements", [])
                        )
                        if "标题" in prev_content:
                            title_block = child
                            break

        if title_block:
            self.update_text_block(doc_id, title_block["block_id"], script.get("title", video_title))

        # --- Step 4: 填充表格 ---
        table_block = next((b for b in page_children if b["block_type"] == 31), None)
        if not table_block:
            raise FeishuError("找不到表格 block")

        result = self._fill_mix_table(doc_id, table_block, script, blocks, block_map)

        # 重新获取（表格填充可能插入了行），然后更新封面标题
        blocks = self.get_blocks(doc_id)
        block_map = {b["block_id"]: b for b in blocks}
        self._update_cover_title_bullet(doc_id, blocks, block_map, title=script.get("title", ""))
        # 更新交付要求中的【标题】和【正文】字段
        self._update_delivery_fields(doc_id, blocks, block_map,
                                     title=script.get("title", ""),
                                     hashtags=script.get("hashtags", []))

        return result

    def _fill_mix_table(self, doc_id: str, table_block: dict, script: dict,
                        blocks: list, block_map: dict) -> str:
        """填充混剪双列表格（内容 | 素材）.

        table.children 是扁平的 cell 列表 (row-major), 不是 row 列表:
          children[0] = cell(0,0) [header], children[1] = cell(0,1) [header]
          children[2] = cell(1,0), children[3] = cell(1,1)
          ...
          children[r*C + c] = cell(r, c)  其中 C=2
        """
        rows_data = script.get("rows", [])
        C = 2  # 混剪表格列数

        # 当前行数（含表头）
        current_row_count = table_block.get("table", {}).get("property", {}).get("row_size", 10)
        needed_rows = len(rows_data) + 1  # +1 for header

        # 插入额外行
        if current_row_count < needed_rows:
            insert_count = needed_rows - current_row_count
            logger.info(f"混剪表格需要插入 {insert_count} 行")
            for i in range(insert_count):
                # row_index 插入位置 = 当前总行数（即末尾之后），每次插入后行数+1
                self.insert_table_row(doc_id, table_block["block_id"],
                                      current_row_count)
                current_row_count += 1
                time.sleep(0.35)

            # 重新获取 blocks
            blocks = self.get_blocks(doc_id)
            block_map = {b["block_id"]: b for b in blocks}
            # 重新获取 table block
            table_block = block_map.get(table_block["block_id"], table_block)

        # 获取扁平 cell 列表
        cell_ids = table_block.get("children", [])
        cells = [block_map.get(cid) for cid in cell_ids if cid in block_map]
        logger.info(f"混剪表格: {len(cells)} 个cells, {current_row_count} 行")

        # 跳过表头行 (row 0), 从 row 1 开始填数据
        for i, (content_text, material_text) in enumerate(rows_data):
            row = i + 1  # 数据行从第1行开始（第0行是表头）
            col0_idx = row * C + 0
            col1_idx = row * C + 1

            if col0_idx < len(cells):
                cell0 = cells[col0_idx]
                child0 = cell0.get("children", [""])[0] if cell0 else None
                if child0:
                    self.update_text_block(doc_id, child0, content_text)

            if col1_idx < len(cells):
                cell1 = cells[col1_idx]
                child1 = cell1.get("children", [""])[0] if cell1 else None
                if child1:
                    self.update_text_block(doc_id, child1, material_text)

            time.sleep(0.15)

        logger.info("混剪表格填充完成")
        return "mix"

    def fill_oral_script(self, doc_id: str, script: dict, video_url: str, video_title: str) -> str:
        """填充口播模板.

        口播模板结构（按 children 顺序）:
          [0] Callout (type=19) → 内嵌"参考视频："文本块
          [1] "标题（可直接参考对标内容）" (type=2)
          [2] Sheet (type=30) — 嵌入表格
          [3] "详情" (type=2)
          [4] 表格 (type=31, 3列, 2行) — 原片文案 | 正式口播脚本 | 图片素材
          [5+] 交付要求 (headings, bullets, grids)
        """
        blocks = self.get_blocks(doc_id)
        block_map = {b["block_id"]: b for b in blocks}

        # 找到 page block
        page = next((b for b in blocks if b["block_type"] == 1), None)
        if not page:
            raise FeishuError("找不到 page block")
        children_ids = page.get("children", [])
        page_children = [block_map[cid] for cid in children_ids if cid in block_map]

        # --- Step 1: 更新页面标题 ---
        # 页面标题在复制时已通过 name 参数设置，无需额外更新

        # --- Step 2: 更新参考视频链接 ---
        callout = next((b for b in page_children if b["block_type"] == 19), None)
        if callout:
            callout_child_ids = callout.get("children", [])
            if callout_child_ids:
                link_block_id = callout_child_ids[0]
                self.update_text_block(doc_id, link_block_id, f"参考视频：{video_url}")

        # --- Step 3: 写入标题到嵌入表格 ---
        sheet_block = next((b for b in page_children if b["block_type"] == 30), None)
        if sheet_block:
            sheet_token = sheet_block.get("sheet", {}).get("token", "")
            if sheet_token:
                self.update_sheet_title(sheet_token, script.get("title", video_title))

        # --- Step 4: 填充三列表格 ---
        table_block = next((b for b in page_children if b["block_type"] == 31), None)
        if not table_block:
            raise FeishuError("找不到表格 block")

        self._fill_oral_table(doc_id, table_block, script, blocks, block_map)

        # --- Step 5: 更新封面标题 + 交付要求字段 ---
        self._update_cover_title_bullet(doc_id, blocks, block_map, title=script.get("title", ""))
        self._update_delivery_fields(doc_id, blocks, block_map,
                                     title=script.get("title", ""),
                                     hashtags=script.get("hashtags", []))

        logger.info("口播模板填充完成")
        return "oral"

    def _fill_oral_table(self, doc_id: str, table_block: dict, script: dict,
                         blocks: list, block_map: dict):
        """填充口播三列表格（原片文案 | 正式口播脚本 | 图片素材）.

        口播模板只有 2 行（1 表头 + 1 数据行）。所有内容填入唯一数据行的 3 列。
        table.children 是扁平的 cell 列表, 布局: [header0, header1, header2, data0, data1, data2]
        """
        dialogs = script.get("dialogs", [])
        images = script.get("images", [])
        original_text = script.get("original_text", "")

        C = 3  # 口播表格列数

        cell_ids = table_block.get("children", [])
        cells = [block_map.get(cid) for cid in cell_ids if cid in block_map]

        logger.info(f"口播表格: {len(cells)} 个cells")

        # 数据行在 row 1（row 0 = 表头）
        # cell(r=1, c=0) = children[1*3+0] = children[3]
        # cell(r=1, c=1) = children[1*3+1] = children[4]
        # cell(r=1, c=2) = children[1*3+2] = children[5]

        if len(cells) < 6:
            logger.warning(f"口播表格 cells 不足，期望6个，实际{len(cells)}个")
            return

        # Col 0: 原片文案 (cell at row=1, col=0)
        cell0 = cells[3]
        child0 = cell0.get("children", [""])[0] if cell0 else None
        if child0:
            self.update_text_block(doc_id, child0, original_text)

        # Col 1: 正式口播脚本 (cell at row=1, col=1)
        cell1 = cells[4]
        child1 = cell1.get("children", [""])[0] if cell1 else None
        if child1:
            dialog_text = "\n\n".join(
                f"**{d[0]}**：{d[1]}" for d in dialogs
            )
            self.update_text_block(doc_id, child1, dialog_text)

        # Col 2: 图片素材 (cell at row=1, col=2)
        cell2 = cells[5]
        child2 = cell2.get("children", [""])[0] if cell2 else None
        if child2:
            images_text = "\n".join(
                f"{i+1}. {img}" for i, img in enumerate(images)
            )
            self.update_text_block(doc_id, child2, images_text)

        logger.info("口播表格填充完成")

    def _update_cover_title_bullet(self, doc_id: str, blocks: list,
                                   block_map: dict, title: str):
        """更新交付要求中封面要求的标题占位符.

        混剪模板 element: "（xxxxx）" (bg=3) → "（{title}）"
        口播模板 element: "标题（学会邪修...）" (bg=3) → "标题（{title}）"
        """
        for block in blocks:
            if block["block_type"] != 12:
                continue

            elements = block.get("bullet", {}).get("elements", [])
            full_text = "".join(e.get("text_run", {}).get("content", "") for e in elements)

            if "封面要求" not in full_text:
                continue

            new_elements = []
            replaced = False
            for e in elements:
                text_run = e.get("text_run", {})
                style = text_run.get("text_element_style", {})
                content = text_run.get("content", "")

                # 找到黄色高亮的标题占位符 (bg=3)
                if style.get("background_color") == 3 and (
                    "xxxxx" in content or "xxx" in content or
                    "学会邪修" in content or "（" in content
                ):
                    # 根据原内容格式决定新内容的格式
                    if content.startswith("标题"):
                        new_content = f"标题（{title}）"
                    elif "xxxxx" in content:
                        new_content = content.replace("xxxxx", title)
                    else:
                        new_content = f"（{title}）"

                    new_elements.append({
                        "text_run": {
                            "content": new_content,
                            "text_element_style": {
                                "bold": style.get("bold", False),
                                "inline_code": False,
                                "italic": False,
                                "strikethrough": False,
                                "underline": False,
                                "background_color": 3,
                            }
                        }
                    })
                    replaced = True
                else:
                    new_elements.append({"text_run": text_run})

            if replaced:
                body = {"update_text_elements": {"elements": new_elements}}
                self._request(
                    "PATCH",
                    f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block['block_id']}",
                    json=body,
                )
                logger.info(f"已更新封面要求标题为: {title}")
                return

    def _update_delivery_fields(self, doc_id: str, blocks: list,
                                 block_map: dict, title: str,
                                 hashtags: list = None):
        """更新交付要求中的【标题】和【正文】字段.

        【标题】→ 脚本标题
        【正文】→ 标题 + 话题词（如：标题文本 #话题1 #话题2）
        【是否发布】和【发布类型】保持模板默认值不变。
        """
        if hashtags is None:
            hashtags = []

        # 构建话题词字符串
        hashtag_str = " ".join(f"#{t.strip('#')}" for t in hashtags) if hashtags else ""

        # 构建【正文】内容：标题 + 话题词
        if hashtag_str:
            body_text = f"{title} {hashtag_str}"
        else:
            body_text = title

        for block in blocks:
            if block["block_type"] != 2:  # text block
                continue

            elements = block.get("text", {}).get("elements", [])
            full_text = "".join(e.get("text_run", {}).get("content", "") for e in elements)

            # 找到包含【标题】和【正文】的文本块
            if "【标题】" not in full_text or "【正文】" not in full_text:
                continue

            new_elements = []
            for e in elements:
                text_run = e.get("text_run", {})
                content = text_run.get("content", "")
                style = text_run.get("text_element_style", {})

                # 替换【标题】行
                if "【标题】" in content:
                    new_content = content.replace("填写标题即可", title)
                    new_elements.append({
                        "text_run": {
                            "content": new_content,
                            "text_element_style": {
                                "bold": style.get("bold", False),
                                "inline_code": False,
                                "italic": False,
                                "strikethrough": False,
                                "underline": False,
                                "background_color": style.get("background_color", 0),
                            }
                        }
                    })
                # 替换【正文】行
                elif "【正文】" in content:
                    new_content = content.replace(
                        "填写标题➕话题词即可", body_text
                    )
                    new_elements.append({
                        "text_run": {
                            "content": new_content,
                            "text_element_style": {
                                "bold": style.get("bold", False),
                                "inline_code": False,
                                "italic": False,
                                "strikethrough": False,
                                "underline": False,
                                "background_color": style.get("background_color", 0),
                            }
                        }
                    })
                else:
                    # 【是否发布】【发布类型】等保持不变
                    new_elements.append({"text_run": text_run})

            body = {"update_text_elements": {"elements": new_elements}}
            self._request(
                "PATCH",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block['block_id']}",
                json=body,
            )
            logger.info(f"已更新交付要求字段: 标题={title}, 正文={body_text}")
            return

        logger.warning("未找到【标题】【正文】字段块，跳过更新")

    # ============================================================
    # 完整流程
    # ============================================================

    def create_and_fill(self, script_type: str, script: dict,
                        video_url: str, video_title: str,
                        seq: int = 1) -> dict:
        """完整流程: 复制模板 → 设权限 → 填内容 → 返回链接.

        Args:
            script_type: "mix" 或 "oral"
            script: 生成的脚本 JSON
            video_url: 抖音视频链接
            video_title: 视频标题
            seq: 文档编号（多脚本模式下区分不同文档）

        Returns:
            {"doc_id": str, "url": str}
        """
        # 1. 复制模板
        result = self.copy_template(script_type, seq=seq)
        doc_id = result["doc_id"]
        doc_url = result["url"]

        # 2. 设置公开权限
        self.set_public_permission(doc_id)

        # 3. 填充内容
        if script_type == "mix":
            self.fill_mix_script(doc_id, script, video_url, video_title)
        else:
            self.fill_oral_script(doc_id, script, video_url, video_title)

        return {"doc_id": doc_id, "url": doc_url}

    def delete_document(self, doc_id: str) -> bool:
        """删除飞书文档."""
        url = f"{FEISHU_BASE_URL}/drive/v1/files/{doc_id}"
        try:
            resp = self._session.delete(url, headers=self._auth_header(), timeout=15)
            if resp.status_code == 200:
                logger.info(f"文档已删除: {doc_id}")
                return True
            # 已经不存在也算成功
            return resp.status_code == 404
        except Exception as e:
            logger.warning(f"删除文档 {doc_id} 异常: {e}")
            return False
