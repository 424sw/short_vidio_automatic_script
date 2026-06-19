"""
飞书 API 客户端：认证、模板复制、权限设置、内容填充、图片操作。
"""
import time
import json
import re
import logging
import requests
from typing import Optional

from config import (
    FEISHU_APP_ID, FEISHU_APP_SECRET,
    FEISHU_AUTH_URL, FEISHU_BASE_URL,
    get_folder_token, get_template_id,
    RETRY_MAX, RETRY_BACKOFF,
    HTTP_TIMEOUT_MEDIUM,
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
        """确保 token 有效，必要时刷新。带重试保护。"""
        if self._token and time.time() < self._token_expires_at:
            return

        logger.info("获取飞书 tenant_access_token...")
        for attempt in range(RETRY_MAX):
            try:
                resp = self._session.post(
                    FEISHU_AUTH_URL,
                    json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
                    timeout=HTTP_TIMEOUT_MEDIUM,
                )
                data = resp.json()
                if resp.status_code == 200 and "tenant_access_token" in data:
                    self._token = data["tenant_access_token"]
                    expire_sec = data.get("expire", 7200)
                    self._token_expires_at = time.time() + expire_sec - 300  # 提前5分钟刷新
                    self._session.headers["Authorization"] = f"Bearer {self._token}"
                    logger.info("飞书 token 获取成功")
                    return
                else:
                    raise FeishuError(f"飞书认证失败: {data}")
            except (requests.RequestException, FeishuError) as e:
                if attempt == RETRY_MAX - 1:
                    raise FeishuError(f"飞书认证失败（已重试 {RETRY_MAX} 次）: {e}")
                time.sleep(RETRY_BACKOFF * (2 ** attempt))

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """带自动 token 刷新的 API 请求."""
        self._ensure_token()

        timeout = kwargs.pop("timeout", HTTP_TIMEOUT_MEDIUM)
        for attempt in range(RETRY_MAX):
            resp = self._session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 401:
                # Token 可能提前过期，刷新后继续下一轮重试
                if attempt < RETRY_MAX - 1:
                    logger.warning("Token 过期 (attempt %d/%d)，正在刷新...", attempt + 1, RETRY_MAX)
                    self._token_expires_at = 0
                    self._ensure_token()
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
                    continue
                # 最后一次尝试，让它落到后面的错误处理

            # 429 限流：等待后重试
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * (2 ** attempt)
                logger.warning(f"飞书限流 429，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue

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
            except Exception:
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
            # 95201: 文档元数据还在初始化（刚复制完模板，飞书后台没准备好）
            # 等待时间递增：1.5s → 3s → 7s，给飞书后台充足时间完成异步初始化
            if code == 95201:
                waits = [1.5, 3.0, 7.0]
                if attempt < len(waits):
                    wait = waits[attempt]
                    logger.warning("飞书文档元数据未就绪(95201)，等待 %.1fs 后重试 (%d/%d)...",
                                  wait, attempt + 1, RETRY_MAX)
                    time.sleep(wait)
                    continue
            # 详细错误日志
            logger.error(f"飞书 API 调用失败: {method} {url}")
            logger.error(f"请求体: {json.dumps(kwargs.get('json', kwargs.get('data', {})), ensure_ascii=False)[:500]}")
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
        template_id = get_template_id(template_type)
        name = generate_doc_title(template_type, seq)

        logger.info(f"复制{template_type}模板 → {name}")
        data = self._request(
            "POST",
            f"{FEISHU_BASE_URL}/drive/v1/files/{template_id}/copy",
            json={
                "name": name,
                "type": "docx",
                "folder_token": get_folder_token(),
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
                          bold: bool = False, background_color: int = None,
                          multiline: bool = False) -> dict:
        """更新文本 block 的内容。

        Args:
            multiline: True 时将 \\n 拆分为多个 text_run element（用于普通文本块换行）。
                       表格单元格内不支持多 element，用单 text_run 包含换行符。
        """
        style = {
            "bold": bold,
            "inline_code": False,
            "italic": False,
            "strikethrough": False,
            "underline": False,
        }
        if background_color is not None:
            style["background_color"] = background_color

        if multiline:
            # 非表格场景：按 \\n 拆分为多个 element
            lines = content.split("\n")
            elements = []
            for i, line in enumerate(lines):
                elements.append({
                    "text_run": {
                        "content": line,
                        "text_element_style": style,
                    }
                })
                if i < len(lines) - 1:
                    elements.append({
                        "text_run": {
                            "content": "\n",
                            "text_element_style": style,
                        }
                    })
        else:
            # 表格单元格：单 text_run，\\n 保留在 content 内
            elements = [{
                "text_run": {
                    "content": content,
                    "text_element_style": style,
                }
            }]

        body = {"update_text_elements": {"elements": elements}}
        return self._request(
            "PATCH",
            f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block_id}",
            json=body,
        )

    def create_block_children(self, doc_id: str, parent_block_id: str,
                              children: list[dict]) -> dict:
        """为指定 block 创建子 block（如为表格单元格追加段落）。"""
        return self._request(
            "POST",
            f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{parent_block_id}/children",
            json={"children": children},
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
            mix_title = script.get("title", video_title)
            self.update_text_block(doc_id, title_block["block_id"], mix_title)

        # --- Step 4: 填充表格 ---
        table_block = next((b for b in page_children if b["block_type"] == 31), None)
        if not table_block:
            raise FeishuError("找不到表格 block")

        result = self._fill_mix_table(doc_id, table_block, script, blocks, block_map)

        # 重新获取（表格填充可能插入了行），然后更新封面标题
        blocks = self.get_blocks(doc_id)
        block_map = {b["block_id"]: b for b in blocks}
        cover_title = re.sub(r'\s*#[^\s#]+', '', script.get("title", video_title)).strip()
        self._update_cover_title_bullet(doc_id, blocks, block_map, title=cover_title)
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
        for i, row in enumerate(rows_data):
            content_text = str(row[0]) if row else ""
            # 按 \n 拆分为段落，每段写为独立的子 text block（飞书中独立 block = 换段/段间距）
            paragraphs = [p for p in content_text.split("\n") if p.strip()]
            logger.info("DEBUG 行%d: %d段, raw=\n%s", i+1, len(paragraphs), content_text.replace("\n", "⏎\n"))
            row = i + 1  # 数据行从第1行开始（第0行是表头）
            col0_idx = row * C + 0
            col1_idx = row * C + 1

            if col0_idx < len(cells) and paragraphs:
                cell0 = cells[col0_idx]
                existing_child_ids = cell0.get("children", []) if cell0 else []
                existing_count = len(existing_child_ids)

                for pi, para in enumerate(paragraphs):
                    if pi < existing_count:
                        # 复用已有的子 text block
                        self.update_text_block(doc_id, existing_child_ids[pi],
                                              para, multiline=False)
                    else:
                        # 创建新的子 text block（段落）
                        self.create_block_children(doc_id, cell0["block_id"], [{
                            "block_type": 2,
                            "text": {
                                "elements": [{
                                    "text_run": {
                                        "content": para,
                                        "text_element_style": {},
                                    }
                                }]
                            }
                        }])
                        time.sleep(0.12)

            # 素材列：暂不开发图片插入，不执行写入操作
            pass

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
        oral_title = script.get("title", video_title)
        oral_hashtags = script.get("hashtags", [])
        full_oral_title = oral_title + " " + " ".join(
            f"#{t.strip('#')}" for t in oral_hashtags) if oral_hashtags else oral_title

        sheet_block = next((b for b in page_children if b["block_type"] == 30), None)
        if sheet_block:
            sheet_token = sheet_block.get("sheet", {}).get("token", "")
            if sheet_token:
                self.update_sheet_title(sheet_token, full_oral_title)

        # --- Step 4: 填充三列表格 ---
        table_block = next((b for b in page_children if b["block_type"] == 31), None)
        if not table_block:
            raise FeishuError("找不到表格 block")

        self._fill_oral_table(doc_id, table_block, script, blocks, block_map)

        # --- Step 5: 更新封面标题 + 交付要求字段 ---
        # 封面要求的"标题"和交付要求的【标题】都不含话题词，【正文】含话题词
        oral_clean_title = re.sub(r'\s*#[^\s#]+', '', oral_title).strip()
        self._update_cover_title_bullet(doc_id, blocks, block_map, title=oral_clean_title)
        self._update_delivery_fields(doc_id, blocks, block_map,
                                     title=oral_title,
                                     hashtags=oral_hashtags)

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

        cell_ids = table_block.get("children", [])
        cells = [block_map.get(cid) for cid in cell_ids if cid in block_map]

        logger.info(f"口播表格: {len(cells)} 个cells")

        if len(cells) < 6:
            logger.warning(f"口播表格 cells 不足，期望6个，实际{len(cells)}个")
            return

        # Col 0: 原片文案 (cell at row=1, col=0)
        cell0 = cells[3]
        child0 = cell0.get("children", [""])[0] if cell0 else None
        if child0:
            self.update_text_block(doc_id, child0, original_text, multiline=True)

        # Col 1: 正式口播脚本 — 【...】标记黄色高亮
        cell1 = cells[4]
        child1 = cell1.get("children", [""])[0] if cell1 else None
        if child1:
            elements = self._build_oral_dialog_elements(dialogs)
            body = {"update_text_elements": {"elements": elements}}
            self._request(
                "PATCH",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{child1}",
                json=body,
            )
            logger.info("口播对话已填充（含黄色高亮）")

        # Col 2: 图片素材 — 暂不开发，不执行写入操作
        pass

        logger.info("口播表格填充完成")

    def _build_oral_dialog_elements(self, dialogs: list) -> list:
        """构建口播对话的富文本 elements，对【...】标记施加黄色高亮。

        飞书 text_element_style.background_color 取值 1-20，3 为浅黄色。
        """
        YELLOW = 3

        elements = []
        for i, d in enumerate(dialogs):
            if i > 0:
                elements.append({
                    "text_run": {"content": "\n", "text_element_style": {}},
                })

            role = d[0]
            content = d[1]

            elements.append({
                "text_run": {"content": role, "text_element_style": {}},
            })
            elements.append({
                "text_run": {"content": "：", "text_element_style": {}},
            })

            parts = re.split(r"(【[^】]+】)", content)
            for part in parts:
                if not part:
                    continue
                if re.match(r"【[^】]+】", part):
                    elements.append({
                        "text_run": {"content": part, "text_element_style": {"background_color": YELLOW}},
                    })
                else:
                    elements.append({
                        "text_run": {"content": part, "text_element_style": {}},
                    })

        return elements

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
                # 保留非 text_run 元素不变
                if "text_run" not in e:
                    new_elements.append(e)
                    continue

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
        """更新交付要求中的标题和话题词字段.

        两阶段匹配：
          1. 精确搜索含【标题】/【正文】标记的块
          2. 回退：搜索含"标题"文本的块 + 位置特征判断
        """
        if hashtags is None:
            hashtags = []

        hashtag_str = " ".join(f"#{t.strip('#')}" for t in hashtags) if hashtags else ""
        # 从 title 中提取不含话题词的干净标题（title 可能已含 "#话题词"）
        clean_title = re.sub(r'\s*#[^\s#]+', '', title).strip()
        # 正文：若干净标题与完整 title 相同（无内嵌话题词），则手动拼接
        if clean_title != title:
            body_text = title  # title 已含话题词，直接用
        elif hashtag_str:
            body_text = f"{title} {hashtag_str}"
        else:
            body_text = title

        # --- 阶段1: 精确匹配【】标记 ---
        matched = False
        for block in blocks:
            bt = block.get("block_type", 0)
            if bt not in (2, 12):
                continue

            key = "text" if bt == 2 else "bullet"
            elements = block.get(key, {}).get("elements", [])
            full_text = "".join(e.get("text_run", {}).get("content", "") for e in elements)

            if "【标题】" not in full_text and "【正文】" not in full_text:
                continue
            matched = True

            new_elements = []
            for e in elements:
                if "text_run" not in e:
                    new_elements.append(e)
                    continue

                text_run = e.get("text_run", {})
                content = text_run.get("content", "")
                style = text_run.get("text_element_style", {})

                base_style = {
                    "bold": style.get("bold", False),
                    "inline_code": False, "italic": False,
                    "strikethrough": False, "underline": False,
                }
                bg = style.get("background_color")
                if bg is not None and bg != 0:
                    base_style["background_color"] = bg

                # 用正则替换标记后的旧内容（两标记在同一 element 内）
                # 【标题】不含话题词，【正文】含话题词
                content = re.sub(
                    r'(【标题】：).*?(?=【|\n)',
                    lambda m: m.group(1) + clean_title,
                    content, count=1)
                content = re.sub(
                    r'(【正文】：).*?(?=【|\n|$)',
                    lambda m: m.group(1) + body_text,
                    content, count=1)

                new_elements.append({
                    "text_run": {
                        "content": content,
                        "text_element_style": base_style,
                    }
                })

            self._request(
                "PATCH",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block['block_id']}",
                json={"update_text_elements": {"elements": new_elements}},
            )
            logger.info(f"已更新交付要求字段（精确）: 标题={title}, 正文={body_text}")

        if matched:
            return

        # --- 阶段2: 回退匹配 — 扫描全部 block（含嵌套）用宽模式匹配 ---
        title_patterns = ["标题：", "标题:", "【标题】", "【标题", "标题】",
                          "填写标题", "title", "标题("]
        body_patterns = ["正文：", "正文:", "【正文】", "【正文", "正文】",
                         "填写标题", "话题词", "xxxxx", "填写正文",
                         "标题➕话题词", "标题 + 话题", "填写话题"]

        title_target = None
        body_target = None

        # 从后往前扫描全部 block（交付字段在尾部）
        for block in reversed(blocks):
            bt = block.get("block_type", 0)
            if bt not in (2, 12):
                continue
            key = "text" if bt == 2 else "bullet"
            elements = block.get(key, {}).get("elements", [])
            full_text = "".join(e.get("text_run", {}).get("content", "") for e in elements)

            if not title_target:
                for pat in title_patterns:
                    if pat in full_text:
                        title_target = block
                        logger.info("回退匹配-标题(pat=%s): %s", pat, full_text[:80])
                        break
            if not body_target:
                for pat in body_patterns:
                    if pat in full_text:
                        body_target = block
                        logger.info("回退匹配-正文(pat=%s): %s", pat, full_text[:80])
                        break
            if title_target and body_target:
                break

        if not title_target and not body_target:
            logger.warning("两阶段均未找到交付字段，dump 全部 text/bullet block:")
            for i, blk in enumerate(blocks):
                bt = blk.get("block_type", 0)
                if bt in (2, 12):
                    key = "text" if bt == 2 else "bullet"
                    full_text = "".join(
                        e.get("text_run", {}).get("content", "")
                        for e in blk.get(key, {}).get("elements", []))
                    if full_text.strip():
                        logger.warning("  [%d] type=%d: %s", i, bt, full_text[:120])
            return

        # --- 辅助：用位置替换的方式更新一个 block ---
        def _replace_after_marker(block, markers, new_val):
            bt = block.get("block_type", 0)
            key = "text" if bt == 2 else "bullet"
            elements = block.get(key, {}).get("elements", [])
            new_elements = []
            replaced = False
            for e in elements:
                if "text_run" not in e:
                    new_elements.append(e)
                    continue
                text_run = e.get("text_run", {})
                content = text_run.get("content", "")
                style = text_run.get("text_element_style", {})
                base_style = {
                    "bold": style.get("bold", False),
                    "inline_code": False, "italic": False,
                    "strikethrough": False, "underline": False,
                }
                bg = style.get("background_color")
                if bg is not None and bg != 0:
                    base_style["background_color"] = bg
                for mk in markers:
                    if mk in content:
                        idx = content.index(mk) + len(mk)
                        content = content[:idx] + new_val
                        replaced = True
                        break
                new_elements.append({
                    "text_run": {
                        "content": content,
                        "text_element_style": base_style,
                    }
                })
            if replaced:
                self._request(
                    "PATCH",
                    f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block['block_id']}",
                    json={"update_text_elements": {"elements": new_elements}},
                )

        # 写入标题（替换各种可能格式）
        title_markers = ["标题：", "标题:", "【标题】", "【标题",
                         "标题】", "标题("]
        if title_target:
            _replace_after_marker(title_target, title_markers, clean_title)
            logger.info("已更新交付标题（回退）: %s", clean_title)

        # 写入正文
        body_markers = ["正文：", "正文:", "【正文】", "【正文",
                        "正文】", "话题词：", "话题词:", "话题：", "话题:"]
        if body_target:
            _replace_after_marker(body_target, body_markers, body_text)
            logger.info("已更新交付正文（回退）: %s", body_text)

        if not title_target and not body_target:
            logger.warning("两阶段均未找到交付字段")

    # ============================================================
    # 图片插入（Step 6）
    # ============================================================

    def _clear_cell_text_children(self, doc_id: str, cell_id: str):
        """清空 Cell 内已有文本子块，避免图片上方出现空白行。"""
        try:
            children_data = self._request(
                "GET",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{cell_id}/children",
                params={"page_size": 50},
            )
            for child in children_data.get("data", {}).get("items", []):
                if child.get("block_type") == 2:  # text block
                    self.update_text_block(doc_id, child["block_id"], "")
        except Exception as e:
            logger.debug("清空 Cell 文本子块异常（非关键）: %s", e)

    def _insert_single_image(self, doc_id: str, cell_id: str,
                             image_bytes: bytes) -> bool:
        """单张图片完整链路（三步串行）：创建占位 → 上传 → 绑定。

        供 insert_all_images() 并行调度，失败返回 False。
        """
        import io as _io
        try:
            # ① 在 Cell 下创建 Image Block 占位
            create_resp = self._request(
                "POST",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{cell_id}/children",
                json={
                    "index": 0,  # 插到最前面，避免在原文本子块下方产生空白行
                    "children": [{"block_type": 27, "image": {}}],
                },
            )

            children = create_resp.get("data", {}).get("children", [])
            if not children:
                logger.warning("创建 Image Block 失败：响应无 children")
                return False
            image_block_id = children[0].get("block_id")
            if not image_block_id:
                logger.warning("创建 Image Block 失败：无 block_id")
                return False

            # ② 上传图片（必须用独立 requests 调用，session 级 Content-Type 会覆盖 multipart）
            self._ensure_token()
            auth_header = {"Authorization": self._session.headers.get("Authorization", "")}

            upload_resp = requests.post(
                f"{FEISHU_BASE_URL}/drive/v1/medias/upload_all",
                headers=auth_header,  # 只传 Auth，让 requests 自动设 multipart Content-Type
                files={"file": ("emoji.png", _io.BytesIO(image_bytes), "image/png")},
                data={
                    "file_name": "emoji.png",
                    "parent_type": "docx_image",
                    "parent_node": image_block_id,
                    "size": str(len(image_bytes)),
                },
                timeout=HTTP_TIMEOUT_MEDIUM,
            )

            if upload_resp.status_code == 401:
                self._token_expires_at = 0
                self._ensure_token()
                auth_header = {"Authorization": self._session.headers.get("Authorization", "")}
                upload_resp = requests.post(
                    f"{FEISHU_BASE_URL}/drive/v1/medias/upload_all",
                    headers=auth_header,
                    files={"file": ("emoji.png", _io.BytesIO(image_bytes), "image/png")},
                    data={
                        "file_name": "emoji.png",
                        "parent_type": "docx_image",
                        "parent_node": image_block_id,
                        "size": str(len(image_bytes)),
                    },
                    timeout=HTTP_TIMEOUT_MEDIUM,
                )

            ur = upload_resp.json()
            if ur.get("code") != 0:
                logger.warning("上传图片失败: code=%s msg=%s", ur.get("code"), ur.get("msg"))
                return False
            file_token = ur["data"]["file_token"]

            # ③ 绑定图片到占位 block
            self._request(
                "PATCH",
                f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{image_block_id}",
                json={"replace_image": {"token": file_token}},
            )

            return True

        except Exception as e:
            logger.warning("图片插入异常: %s", e)
            return False

    def insert_all_images(self, doc_id: str, script: dict, script_type: str,
                          matcher) -> dict:
        """Step 6 并行编排：匹配 → 下载 → 插入全部图片。

        Returns:
            {"total": int, "success": int, "failed": int}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from config import IMAGE_INSERT_WORKERS

        # 1. 提取所有素材描述
        if script_type == "mix":
            rows = script.get("rows", [])
            descriptions = [str(r[1]) if len(r) >= 2 else "" for r in rows]
        else:
            descriptions = script.get("images", [])

        if not descriptions:
            logger.info("无素材描述，跳过图片插入")
            return {"total": 0, "success": 0, "failed": 0}

        total = len(descriptions)
        logger.info("Step 6: 共 %d 条素材描述待匹配", total)

        # 2. 匹配 + 并行下载
        matches = matcher.match_all(descriptions)
        hit_count = sum(1 for m in matches if m)
        logger.info("匹配结果: %d/%d 命中关键词, 其余降级兜底", hit_count, total)

        downloads = matcher.download_all(matches)
        ready = [(i, d) for i, d in enumerate(downloads) if d and d.get("image_bytes")]
        logger.info("下载完成: %d/%d 张图片就绪", len(ready), total)

        # 3. 找到每个描述对应的目标 Cell
        blocks = self.get_blocks(doc_id)
        block_map = {b["block_id"]: b for b in blocks}
        page = next((b for b in blocks if b["block_type"] == 1), None)
        if not page:
            logger.warning("找不到 page block，无法插入图片")
            return {"total": total, "success": 0, "failed": total}

        page_children = [block_map[cid] for cid in page.get("children", []) if cid in block_map]
        table_block = next((b for b in page_children if b["block_type"] == 31), None)
        if not table_block:
            logger.warning("找不到表格 block，无法插入图片")
            return {"total": total, "success": 0, "failed": total}

        cell_ids = table_block.get("children", [])
        cells = [block_map.get(cid) for cid in cell_ids if cid in block_map]

        # 构建 (desc_idx, cell_block_id, image_bytes) 任务列表
        tasks = []
        if script_type == "mix":
            C = 2
            for desc_idx, dl in ready:
                row = desc_idx + 1  # desc_idx = 原始行号（0-based），+1 跳过表头
                col_idx = row * C + 1  # 素材列
                if col_idx < len(cells) and cells[col_idx]:
                    tasks.append((desc_idx, cells[col_idx]["block_id"], dl["image_bytes"]))
        else:
            # 口播：所有图片插入到 Col 2（唯一数据行的第 3 列）
            # 口播表格: cells[0]=header0, [1]=header1, [2]=header2, [3]=data0, [4]=data1, [5]=data2
            if len(cells) >= 6 and cells[5]:
                cell_id = cells[5]["block_id"]
                for desc_idx, dl in ready:
                    tasks.append((desc_idx, cell_id, dl["image_bytes"]))

        if not tasks:
            logger.warning("无有效 Cell 可供插入图片")
            return {"total": total, "success": 0, "failed": total}

        # 3.5 清除目标 Cell 中的已有空文本子块（消除图片上方的空白行）
        unique_cells = set(cell_id for _, cell_id, _ in tasks)
        for cell_id in unique_cells:
            self._clear_cell_text_children(doc_id, cell_id)

        # 4. 并行插入
        success = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=IMAGE_INSERT_WORKERS) as executor:
            futures = {
                executor.submit(
                    self._insert_single_image, doc_id, cell_id, img_bytes
                ): desc_idx
                for desc_idx, cell_id, img_bytes in tasks
            }
            for future in as_completed(futures):
                desc_idx = futures[future]
                try:
                    if future.result():
                        success += 1
                    else:
                        failed += 1
                        logger.warning("图片 %d 插入失败", desc_idx + 1)
                except Exception as e:
                    failed += 1
                    logger.warning("图片 %d 插入异常: %s", desc_idx + 1, e)

        # 未匹配或下载失败的也算入 failed
        failed += total - len(ready)
        logger.info("Step 6 完成: total=%d success=%d failed=%d", total, success, failed)
        return {"total": total, "success": success, "failed": failed}

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
        """删除飞书文档。"""
        try:
            self._request("DELETE", f"{FEISHU_BASE_URL}/drive/v1/files/{doc_id}",
                          params={"type": "docx"}, timeout=HTTP_TIMEOUT_MEDIUM)
            logger.info(f"文档已删除: {doc_id}")
            return True
        except FeishuError:
            # 已经不存在也算成功
            return False
        except Exception as e:
            logger.warning(f"删除文档 {doc_id} 异常: {e}")
            return False
