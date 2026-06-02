# 短视频自动化脚本生成系统

## 项目背景

### 目标
自动化生成短视频脚本（口播 + 混剪），输入抖音视频链接和飞书模板，输出填好的飞书文档。

### 工作流
```
抖音链接 → 提取视频 → 全模态AI分析 → 结合模板+示例 → 生成脚本 → 填入飞书副本
```

### 两阶段
| 阶段 | 说明 |
|------|------|
| **Phase 1（当前）** | Claude Code 内 MCP 手动跑通流程 |
| **Phase 2（未来）** | React + FastAPI 独立应用 |

---

## 关键凭证

| 资源 | 值 |
|------|-----|
| **全模态 AI API** | `https://apihub.agnes-ai.com/v1/chat/completions` |
| **AI API Key** | `sk-ZhwA3nuflKAXF2KkkcBJFj1oJwUk5GnyOoMTk2xkudKhX9L9` |
| **可用模型** | `agnes-2.0-flash`（文本+vision），`agnes-video-v2.0`（视频，当前不可用） |
| **飞书 App ID** | `cli_aa97347bb5f9dbd7` |
| **飞书 App Secret** | `UnGjpZgesVm4e0OKKkX5AEARIKiji4RC` |
| **FFmpeg 路径** | `C:\Users\15769\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe` |

---

## 飞书文档 ID 映射

| 文档 | node_token | obj_token (doc_id) |
|------|-----------|---------------------|
| 混剪模板 | `MnKRwLm1dihCpCk8QpAcivZZnRz` | `B1HtdfhjKo4g4QxgNNncCtVwnth` |
| 口播模板 | `SXcLwR1maiy97VkTVihcdSann3e` | `EbLGdZ2qYoQgpixsmQjc5EkjnNf` |
| 混剪示例 | `ECarwI4KMiOPEeklvMEcGs5CnLH` | `PZAkd0ZU1o5K4ZxYLEJcBt2Enjb` |
| 口播示例 | `SEYawH24XikmtBk1xl6caqqbnwh` | `JuQUduPH0o6PWUxoeI0cwoh6nYg` |

知识库 space_id: `7644759744296684763`

---

## Phase 1 测试 Skill

### 执行步骤

#### Step 1: 获取抖音视频
```python
# 用 iesdouyin.com 接口提取
share_url = f'https://www.iesdouyin.com/share/video/{video_id}'
# 使用 iPhone UA header
# 解析 window._ROUTER_DATA → loaderData → video_(id)/page → videoInfoRes
# 取 video["play_addr"]["url_list"][0].replace("playwm", "play")
# 下载后保存为 test_video.mp4
```

#### Step 2: AI 分析视频
```bash
# FFmpeg 提取关键帧（每5秒1帧）
ffmpeg -i test_video.mp4 -vf "fps=1/5" -q:v 2 frames/frame_%03d.jpg
```
```python
# 用 agnes-2.0-flash vision 逐帧分析
# Content: [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}, {"type": "text", "text": "描述画面"}]
# 汇总所有帧描述 → 再次调用 AI 综合成完整的视频理解
```

#### Step 3: 读取飞书文档
```python
# 用飞书 Docx API 的 raw_content 读取模板和示例
GET /open-apis/docx/v1/documents/{doc_id}/raw_content
```

#### Step 4: 生成脚本
用 agnes-2.0-flash 根据视频分析 + 模板结构 + 示例风格生成两份脚本。

**脚本要求**：
- 鱼泡直聘软广在 ≈50% 位置，是"第一个"推荐
- 混剪：15+ 行内容\|素材双栏表格
- 口播：20+ 轮 A/B 对话，每句后【情绪标记】
- 两份脚本重复率 < 40%
- 不写"交付要求"模块内容
- 标题格式：`日期+混剪/口播脚本+编号`

#### Step 5: 复制模板（关键！保留版式）
```python
# 获取 tenant_access_token
POST /open-apis/auth/v3/tenant_access_token/internal

# 复制模板到 app 可写的文件夹
POST /open-apis/drive/v1/files/{template_obj_token}/copy
Body: {"name": "2026.06.02+混剪脚本+001", "type": "docx", "folder_token": "nodcnfKha8zoI7HaoGIBOg7D4Hh"}
```

**注意**：`folder_token` 必须用 app 有写权限的文件夹。当前使用 `nodcnfKha8zoI7HaoGIBOg7D4Hh`。

#### Step 6: 读取副本 Block 结构
```python
GET /open-apis/docx/v1/documents/{copy_id}/blocks?page_size=500
```
遍历所有 block，记录每个需要填入的 block_id。

#### Step 7: 填入文本内容
```python
# 更新文本 block
PATCH /open-apis/docx/v1/documents/{doc_id}/blocks/{block_id}
Body: {"update_text_elements": {"elements": [
    {"text_run": {"content": "文本", "text_element_style": {"bold": False}}}
]}}

# 黄色高亮（情绪标记）
# 给 text_element_style 加 "background_color": 3
```

#### Step 8: 图片处理（TODO）
```python
# 1. WebSearch 搜索表情包图片
# 2. 下载图片
# 3. 上传飞书
POST /open-apis/drive/v1/medias/upload_all
# 4. 插入到文档 block 中（inline_file）
```

### 已验证的 API 行为

| API | 方法 | 要点 |
|-----|------|------|
| `update_text_elements` | PATCH | ✅ 可用，body: `{"update_text_elements": {"elements": [...]}}` |
| `replace_text` | PATCH | ❌ 返回 1770001 invalid param |
| `batch_update` | PATCH/POST | ❌ 不可用 |
| `drive/v1/files/{id}/copy` | POST | ✅ 需要正确的 folder_token |
| `docx/v1/documents/import` | POST | ❌ Markdown 导入丢失版式，不推荐 |

### 当前状态
- 混剪副本：`BgTcd26lwo3ktgx4rNdcjpiJnCY`（9行已填，缺6行 + 图片）
- 口播副本：`XBBvd2remoSevkxdYjRcDN06n1b`（全部文本已填，缺黄色高亮 + 图片）
- 飞书云文档中仅保留这两个副本

---

## 项目文件结构

```
short_vidio_automatic_script/
├── CLAUDE.md              # 本文件（项目文档 + 测试 skill）
├── .mcp.json              # MCP 配置（feishu + douyin）
├── api_state.json         # 运行时状态（token、doc IDs）
├── data/                  # 参考数据
│   ├── video_info.json    # 抖音视频元数据
│   ├── video_synthesis.json # AI 视频综合分析
│   ├── frame_analysis.json  # 逐帧分析结果
│   ├── blocks_*.json      # 模板/副本 block 结构
│   └── copy_blocks_*.json # 副本 block 结构
└── output/                # 生成的脚本
    ├── scripts_v3.json    # 最新脚本 JSON
    └── generated_scripts.json # 脚本（含修复）
```

---

## 已知问题 & 下一步

1. **图片素材**：目前只有文字描述，需要搜索→上传→插入真实图片
2. **黄色高亮**：Block API 的 `background_color: 3` 已确认可用，待批量应用
3. **混剪行数**：模板只有 19 对空行（填了 9 行），需用 API 插入更多行或复用剩余空行
4. **全模态视频模型**：`agnes-video-v2.0` 返回 404，当前用逐帧 vision 替代
