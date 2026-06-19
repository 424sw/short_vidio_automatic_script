"""验证 TableCell 内嵌 Image Block 可行性 — 基于真实模板。

流程：
1. 从表情包库下载一张图片
2. 复制混剪模板（和项目生产一致）
3. 找到模板表格的素材列 Cell
4. 往 Cell 的 children 创建 Image Block (block_type=27)
5. 上传图片 + replace_image 绑定
6. 设公开权限，返回链接
"""
import sys, os, json, requests, io
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_AUTH_URL, FEISHU_BASE_URL,
    get_folder_token, get_template_id,
)


def get_token():
    resp = requests.post(FEISHU_AUTH_URL,
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=15)
    return resp.json()["tenant_access_token"]


def download_sample_image(headers):
    """从表情包库下载第一张图片。"""
    EMOJI_DOC = "S8HRdTVmToAPArxMeWRci0iBn4T"
    page_token = None
    for _ in range(3):
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{FEISHU_BASE_URL}/docx/v1/documents/{EMOJI_DOC}/blocks",
            params=params, headers=headers, timeout=30)
        data = resp.json().get("data", {})
        for b in data.get("items", []):
            if b.get("block_type") == 27 and b.get("image", {}).get("token"):
                img = b["image"]
                dl = requests.get(
                    f"{FEISHU_BASE_URL}/drive/v1/medias/{img['token']}/download",
                    headers=headers, timeout=30)
                if dl.status_code == 200:
                    return dl.content, img
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    raise Exception("表情包库中未找到可下载的图片")


def copy_template(headers, template_type="mix"):
    """复制混剪模板（和项目 FeishuClient.copy_template 一致）。"""
    template_id = get_template_id(template_type)
    name = f"图片嵌入验证_{datetime.now().strftime('%H%M%S')}"
    resp = requests.post(
        f"{FEISHU_BASE_URL}/drive/v1/files/{template_id}/copy",
        json={"name": name, "type": "docx", "folder_token": get_folder_token()},
        headers=headers, timeout=15)
    r = resp.json()
    if r.get("code") != 0:
        raise Exception(f"复制模板失败: {r}")
    return r["data"]["file"]["token"], r["data"]["file"]["url"]


def get_all_blocks(headers, doc_id):
    """获取文档所有 blocks（分页）。"""
    all_blocks = []
    page_token = None
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks",
            params=params, headers=headers, timeout=30)
        data = resp.json().get("data", {})
        all_blocks.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return all_blocks


def set_public(headers, doc_id):
    requests.patch(
        f"{FEISHU_BASE_URL}/drive/v1/permissions/{doc_id}/public",
        params={"type": "docx"},
        json={"link_share_entity": "anyone_editable",
              "external_access": True, "invite_external": True},
        headers=headers, timeout=15)


def main():
    print("=" * 60)
    print("验证: 模板表格素材列 Cell → 内嵌 Image Block")
    print("=" * 60)

    headers = {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json",
    }

    # Step 1: 从表情包库下载图片
    print("\n[1/5] 从表情包库下载测试图片...")
    img_bytes, img_meta = download_sample_image(headers)
    print(f"  下载完成: {len(img_bytes)} bytes, {img_meta['width']}×{img_meta['height']}")

    # Step 2: 复制模板
    print("\n[2/5] 复制混剪模板...")
    doc_id, doc_url = copy_template(headers)
    print(f"  doc_url: {doc_url}")

    # Step 3: 解析模板结构，找到素材列 Cell
    print("\n[3/5] 解析模板表格结构...")
    blocks = get_all_blocks(headers, doc_id)
    block_map = {b["block_id"]: b for b in blocks}

    # 找 page block
    page = next((b for b in blocks if b["block_type"] == 1), None)
    if not page:
        raise Exception("找不到 Page Block")
    page_children_ids = page.get("children", [])

    # 找表格 (type=31)
    table_block = None
    for bid in page_children_ids:
        b = block_map.get(bid)
        if b and b["block_type"] == 31:
            table_block = b
            break
    if not table_block:
        raise Exception("找不到表格 Block")

    # 表格的 children 是扁平的 cell ID 列表 (row-major)
    # 混剪模板: 2列 (0=文案, 1=素材), 第0行是表头
    C = table_block.get("table", {}).get("property", {}).get("column_size", 2)
    cell_ids = table_block.get("children", [])
    cells = [(cid, block_map.get(cid)) for cid in cell_ids if cid in block_map]

    print(f"  表格列数={C}, 总cells={len(cells)}, 总行数={len(cells)//C}")

    # 找第一个数据行的素材列 Cell (row=1, col=1)
    data_row = 1
    target_col = 1  # 素材列
    target_idx = data_row * C + target_col

    if target_idx >= len(cells):
        # 可能表头行不存在（header_row=False），调整
        target_idx = C + 1  # row=1 (第2行), col=1

    target_cell_id, target_cell = cells[target_idx]
    print(f"  目标 Cell: idx={target_idx}, block_id={target_cell_id}")
    print(f"  当前 Cell children: {target_cell.get('children', [])}")

    # Step 4: 往素材列 Cell 创建 Image Block
    print("\n[4/5] 在素材列 Cell 创建 Image Block...")

    create_resp = requests.post(
        f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{target_cell_id}/children",
        params={"document_revision_id": "-1"},
        json={
            "index": -1,  # 追加到末尾（如果有已有 text block 的话）
            "children": [{
                "block_type": 27,
                "image": {},
            }],
        },
        headers=headers,
        timeout=15,
    )
    cr = create_resp.json()
    print(f"  Create Image Block: code={cr.get('code')}, msg={cr.get('msg')}")

    if cr.get("code") != 0:
        print(f"\n  ❌ 方案 A 失败: 无法在 TableCell 下创建 Image Block")
        print(f"  错误: {json.dumps(cr, ensure_ascii=False)[:500]}")
        print(f"\n  → 需要走方案 B（表格下方独立放图）或方案 D（Bitable）")
        # 仍然公开文档，让用户看到当前模板结构
        set_public(headers, doc_id)
        print(f"  文档链接（无图片）: {doc_url}")
        return

    # 提取 image_block_id
    image_block_id = None
    children_data = cr.get("data", {}).get("children", [])
    if children_data:
        image_block_id = children_data[0].get("block_id")
    if not image_block_id:
        # 可能直接在 data 层级
        image_block_id = cr.get("data", {}).get("block_id")
    print(f"  Image Block ID: {image_block_id}")

    # Step 5: 上传图片 + replace_image
    print("\n[5/5] 上传图片并绑定...")

    # Upload (size 必填)
    upload_resp = requests.post(
        f"{FEISHU_BASE_URL}/drive/v1/medias/upload_all",
        headers={"Authorization": headers["Authorization"]},
        files={"file": ("test_emoji.png", io.BytesIO(img_bytes), "image/png")},
        data={
            "file_name": "test_emoji.png",
            "parent_type": "docx_image",
            "parent_node": image_block_id,
            "size": str(len(img_bytes)),
        },
        timeout=30,
    )
    ur = upload_resp.json()
    print(f"  Upload: code={ur.get('code')}, msg={ur.get('msg')}")
    if ur.get("code") != 0:
        print(f"  上传失败: {ur}")
        set_public(headers, doc_id)
        print(f"  文档链接: {doc_url}")
        return
    file_token = ur["data"]["file_token"]
    print(f"  file_token: {file_token}")

    # Replace image
    replace_resp = requests.patch(
        f"{FEISHU_BASE_URL}/docx/v1/documents/{doc_id}/blocks/{image_block_id}",
        params={"document_revision_id": "-1"},
        json={"replace_image": {"token": file_token}},
        headers=headers,
        timeout=15,
    )
    rr = replace_resp.json()
    print(f"  Replace Image: code={rr.get('code')}, msg={rr.get('msg')}")

    # 公开文档
    set_public(headers, doc_id)

    print(f"""
{'=' * 60}
✅ 验证文档已创建，请打开查看：
🔗 {doc_url}

查看要点：
  1. 找到"图文配置"下方的表格
  2. 表格第 2 行（第一个数据行）的右侧「素材」列
  3. 是否能看到表情包图片？

✅ 有图片 → 方案 A 可行
❌ 空白   → 方案 A 不可行，走方案 B/D
{'=' * 60}
""")


if __name__ == "__main__":
    main()
