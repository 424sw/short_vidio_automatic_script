# 短视频自动化脚本生成系统

## 项目概述

### 目标
自动化生成短视频脚本，输入抖音视频链接和飞书模板，AI 分析视频内容后填入飞书副本。

### 工作流
```
抖音链接 → 提取视频 → 全模态AI分析 → 生成脚本 → 填入飞书副本
```

### 两阶段
| 阶段 | 说明 |
|------|------|
| **Phase 1（已完成）** | Claude Code 内 MCP 手动跑通完整流程 |
| **Phase 2（待开发）** | React + FastAPI 独立应用 |

---

## 关键凭证

| 资源 | 值 |
|------|-----|
| **全模态 AI API** | `https://apihub.agnes-ai.com/v1/chat/completions` |
| **AI API Key** | `sk-ZhwA3nuflKAXF2KkkcBJFj1oJwUk5GnyOoMTk2xkudKhX9L9` |
| **可用模型** | `agnes-2.0-flash`（文本+vision）；`agnes-video-v2.0`（视频，不可用） |
| **飞书 App ID** | `cli_aa97347bb5f9dbd7` |
| **飞书 App Secret** | `UnGjpZgesVm4e0OKKkX5AEARIKiji4RC` |
| **飞书 App 文件夹** | `nodcnfKha8zoI7HaoGIBOg7D4Hh`（app 有写权限） |
| **FFmpeg 路径** | `C:\Users\15769\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe` |

### 飞书 Token 获取
```python
POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
Body: {"app_id": "cli_aa97347bb5f9dbd7", "app_secret": "UnGjpZgesVm4e0OKKkX5AEARIKiji4RC"}
# Response: {"tenant_access_token": "t-..."}  有效期 2 小时
```

---

## 飞书文档 ID 映射

| 文档 | node_token | obj_token (doc_id) | 用途 |
|------|-----------|---------------------|------|
| 混剪模板 | `MnKRwLm1dihCpCk8QpAcivZZnRz` | `B1HtdfhjKo4g4QxgNNncCtVwnth` | 复制模板生成混剪副本 |
| 口播模板 | `SXcLwR1maiy97VkTVihcdSann3e` | `EbLGdZ2qYoQgpixsmQjc5EkjnNf` | 复制模板生成口播副本 |
| 混剪示例 | `ECarwI4KMiOPEeklvMEcGs5CnLH` | `PZAkd0ZU1o5K4xZYLEJcBt2Enjb` | 混剪格式参考 |
| 口播示例 | `SEYawH24XikmtBk1xl6caqqbnwh` | `JuQUduPH0o6PWUxoeI0cwoh6nYg` | 口播格式参考 |

知识库 space_id: `7644759744296684763`

---
---

## 输出要求（关键规则）

### 核心原则
**不同类型的参考视频对应不同类型的脚本**。例如：图文类视频 → 只出混剪脚本；剧情类视频 → 只出口播脚本。同一视频的两种形式脚本内容应高度一致，仅呈现形式不同。

### 标题规则
- 文档页面标题格式：`日期+混剪/口播脚本+编号`（如 `2026.06.02+混剪脚本+001`）
- 脚本主标题：由 AI 生成，反映视频核心内容
- 口播标题写入**嵌入表格（Sheet）**的 `A2` 格，不是页面文本标签（详见后文）
- "交付要求" > "封面要求"中的标题 **不含 #标签**（如 `面试时别再说这三句话了，HR听了直接pass`）

### 混剪脚本格式
- 双栏表格（内容 | 素材），表头行固定
- 内容列：口播文案文本，按场景逐行
- 素材列：对应的配图/表情包描述（如 `功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包`）
- 行数根据视频内容决定（9-15 行），不够用 API 插入
- 鱼泡直聘软广在 **≈50% 位置**

### 口播脚本格式
- 三栏表格（原片文案 | 正式口播脚本 | 图片素材）
- "正式口播脚本"列：20 轮 A/B 对话，每轮末尾【情绪标记】黄色高亮
- "原片文案"列：纯叙述文本（无角色对话）
- "图片素材"列：编号的图片描述列表

### 交付要求模块
- **不要修改模板内的任何格式和内容**
- **仅修改"封面要求"中的标题占位符**（`bg=3` 黄色高亮的标题文本）
- 其他所有 bullet（剪辑要求、拍摄要求等）保持模板原样不触动

---
---

## Phase 1 完整测试流程

### Step 1: 获取抖音视频
```python
share_url = f'https://www.iesdouyin.com/share/video/{video_id}'
# 使用 iPhone UA header
# 解析 window._ROUTER_DATA → loaderData → video_(id)/page → videoInfoRes
# 取 video["play_addr"]["url_list"][0].replace("playwm", "play")
# 下载后保存为 test_video.mp4
```

### Step 2: AI 分析视频
```bash
ffmpeg -i test_video.mp4 -vf "fps=1/5" -q:v 2 frames/frame_%03d.jpg
```
```python
# agnes-2.0-flash vision 逐帧分析（base64 编码图片）
# Content: [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}, {"type": "text", "text": "描述画面"}]
# 汇总所有帧描述 → 再次调用 AI 综合成完整视频理解 → 保存至 data/video_synthesis.json
```

### Step 3: 读取飞书模板/示例
```python
GET /open-apis/docx/v1/documents/{doc_id}/raw_content       # 纯文本内容
GET /open-apis/docx/v1/documents/{doc_id}/blocks?page_size=500   # Block 结构
GET /open-apis/docx/v1/documents/{doc_id}/blocks/{block_id}      # 单个 Block
```

### Step 4: 生成脚本
用 `agnes-2.0-flash` 根据视频分析 + 模板结构 + 示例格式生成脚本。输出保存至 `output/` 目录。

### Step 5: 复制模板
```python
POST /open-apis/drive/v1/files/{template_obj_token}/copy
Body: {
    "name": "2026.06.02+混剪脚本+001",
    "type": "docx",
    "folder_token": "nodcnfKha8zoI7HaoGIBOg7D4Hh"
}
# Response: data.file.token = 新 doc_id, data.file.url = 链接
```

**注意**：用 `tenant_access_token` 复制 → 副本归 app 所有（不影响功能）。

### Step 6: 设置权限
```python
PATCH /open-apis/drive/v1/permissions/{doc_id}/public?type=docx
Body: {
    "link_share_entity": "anyone_editable",   # 互联网所有人可编辑
    "external_access": true,
    "invite_external": true
}
```

权限参数速查：
| `link_share_entity` | 效果 |
|---------------------|------|
| `tenant_readable` | 组织内只读 |
| `tenant_editable` | 组织内可编辑 |
| `anyone_readable` | 互联网只读 |
| `anyone_editable` | 互联网可编辑 |

### Step 7: 读取副本 Block 结构
```python
GET /open-apis/docx/v1/documents/{copy_id}/blocks?page_size=500
```
根据 block_type 遍历，记录需要填入的 block_id：
- type=1: 页面标题
- type=2: 文本块（标题行、视频链接等）
- type=32: 表格单元格（含子文本块）
- type=30: 嵌入表格（口播标题）
- type=12: 子弹列表（交付要求）

### Step 8: 填入内容
```python
# 更新文本块
PATCH /open-apis/docx/v1/documents/{doc_id}/blocks/{block_id}
Body: {"update_text_elements": {"elements": [
    {"text_run": {"content": "文本内容", "text_element_style": {
        "bold": False, "inline_code": False, "italic": False,
        "strikethrough": False, "underline": False
    }}}
]}}
```

### Step 9: 口播标题写入嵌入表格（Sheet Block）
模板和示例中 type=30 的 sheet block 内嵌了一个电子表格，标题写在这里。

```python
# 1. 获取 sheet token
GET /open-apis/docx/v1/documents/{doc_id}/blocks?page_size=500
# → 找到 block_type=30 的 block，取 sheet.token

# 2. 去掉 _gWovo0 后缀得到 spreadsheetToken
spreadsheet_token = sheet_token.replace("_gWovo0", "")

# 3. 写入标题到 A2 格
PUT /open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values
Body: {"valueRange": {"range": "gWovo0!A2:B2", "values": [["标题文本", "否"]]}}

# 4. 读取验证
GET /open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/gWovo0
```

### Step 10: 插入表格行（如需更多行）
```python
# batch_update 已确认可用
POST /open-apis/docx/v1/documents/{doc_id}/blocks/batch_update
Body: {"requests": [{"block_id": "{table_block_id}", "insert_table_row": {"row_index": N}}]}
# row_index 0-based, 插入后原行下移
# 建议间隔 0.3s 避免限流
```

---

## 已验证的 API 行为

| API | 端点 | 状态 | 要点 |
|-----|------|------|------|
| `update_text_elements` | `PATCH /blocks/{id}` | ✅ | 更新文本内容，支持 `background_color` |
| `batch_update` → `insert_table_row` | `PATCH /blocks/batch_update` | ✅ | 向表格插入新行，可用！ |
| `drive/v1/files/{id}/copy` | `POST` | ✅ | 复制模板，需正确 `folder_token` |
| `drive/v1/permissions/{id}/public` | `PATCH` | ✅ | 设置公开链接权限 |
| `sheets/v2/spreadsheets/{}/values` | `PUT` | ✅ | 写入嵌入表格 |
| `drive/v1/medias/upload_all` | `POST` | ✅ | 上传图片，返回 `file_token` |
| `children` 创建 image block | `POST /blocks/{id}/children` | ❌ | `1770001 invalid param`，无法创建图片块 |
| `descendant` 创建 image block | `POST /blocks/{id}/descendant` | ❌ | 同上 |
| `update_text_elements` `inline_file` | `PATCH /blocks/{id}` | ❌ | `1770024 invalid operation` |
| `batch_update` `update_image` | `PATCH /blocks/batch_update` | ❌ | `1770001` |
| `replace_text` | `PATCH` | ❌ | 不可用 |
| `docx/v1/documents/import` | `POST` | ❌ | Markdown 导入丢失版式，不推荐 |

### 黄色高亮
```json
"text_element_style": {..., "background_color": 3}
```
- `background_color: 3` = 黄色高亮
- `background_color: 10` = 另一种高亮色（模板中"黄色字体"使用）

### 文本元素样式字段
```json
{
    "bold": false, "inline_code": false, "italic": false,
    "strikethrough": false, "underline": false,
    "background_color": 3   // 可选，3=黄色
}
```
`link` 字段从未在实际文档中出现。`#标签` 的可点击行为由飞书客户端渲染层自动处理，API 写入纯文本即可。

---

## 模板结构关键字段

### 混剪模板结构
```
Page (type=1)
├── 视频形式参考视频 (type=2)
├── Callout (type=19)
│   └── [视频链接] (type=2)
├── 标题（剪辑不用管）(type=2, bold, bg=3高亮)
├── [空行：填入脚本标题] (type=2)
├── 图文配置 (type=2)
├── Table 10r×2c (type=31)
│   ├── [0,0] 内容 [0,1] 素材 — header
│   └── [1-9,0-1] — 数据行
├── 交付要求 (type=5, heading3)
│   ├── 剪辑类：视频比例/音效/字体... (type=12, bullet)
│   └── 封面要求：表情包+标题（xxxxx），黄色字体，抖音体
└── 表情包：/ 参考图： (type=2)
```

### 口播模板结构
```
Page (type=1)
├── Callout (type=19) → 参考视频链接
├── 标题（可直接参考对标内容）(type=2, bold)
├── Sheet (type=30) → 嵌入表格 gWovo0
│   ├── A1="标题" B1="是否选中"
│   └── A2=[填入标题] B2="否"
├── 详情 (type=2, bold)
├── Table 2r×3c (type=31)
│   ├── [0,0] 原片文案 [0,1] 正式口播脚本 [0,2] 图片素材
│   └── [1,0] 原片文案内容 [1,1] A/B对话 [1,2] 图片描述
├── 交付要求 (type=5)
│   ├── 拍摄类：场景/穿搭/镜头/动作/情绪...
│   ├── 剪辑类：音效/背景音乐/字幕...
│   └── 封面要求：...上面标题（xxxxx）加文本框...
└── 参考图 (type=2)
```

---

## Windows 编码配置

Git Bash 在中文 Windows 下 `PYTHONIOENCODING` 为空，Python stdout 默认 `gbk`，导致中文乱码。

**已在 `~/.bashrc` 中永久修复：**
```bash
export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export LC_CTYPE=en_US.UTF-8
```
新终端自动生效；当前会话需带 `export PYTHONIOENCODING=utf-8` 前缀运行 Python。

---

## 已知问题 & 后续工作

### 1. 图片素材（重要，暂未解决）
- 图片**上传** API 正常：`POST /drive/v1/medias/upload_all` → 返回 `file_token`
- 图片**插入**文档：所有尝试均失败（`children`、`descendant`、`inline_file`、`batch_update`）
- 当前替代方案：在素材列写入文字描述，由剪辑师手动搜索对应素材
- 后续可尝试：查阅飞书开放平台 image block 的具体创建文档，或联系飞书技术支持

### 2. 全模态视频模型
`agnes-video-v2.0` 返回 404，当前用逐帧 vision 分析替代，效果可接受

### 3. Phase 2 开发
- React 前端：用户输入抖音链接 + 选择脚本类型
- FastAPI 后端：
  - 调用抖音 API 提取视频 + `agnes-2.0-flash` 分析
  - 调用飞书 API：复制模板 → 设置权限 → 填入内容
- 所有 API 调用模式已在 Phase 1 验证，可直接复用
- 使用 `tenant_access_token` 即可，文档所有权不影响功能

---

## 项目文件结构

```
short_vidio_automatic_script/
├── CLAUDE.md                    # 项目文档（本文件）
├── .mcp.json                    # MCP 配置（feishu + douyin）
├── .gitignore
├── api_state.json               # 运行时状态（token、副本 doc IDs）
├── data/
│   ├── video_info.json          # 抖音视频元数据
│   ├── video_synthesis.json     # AI 视频综合分析
│   ├── frame_analysis.json      # 逐帧分析结果
│   ├── blocks_混剪模板.json     # 混剪模板 block 结构
│   └── blocks_口播模板.json     # 口播模板 block 结构
└── output/
    └── scripts_v3.json          # 最新生成的脚本
```

`api_state.json` 格式：
```json
{
  "token": "t-...",
  "mix_template": "B1HtdfhjKo4g4QxgNNncCtVwnth",
  "oral_template": "EbLGdZ2qYoQgpixsmQjc5EkjnNf",
  "mix_copy_id": "SJLFdRabOolUMqx0zVjcdeVEnGf",
  "oral_copy_id": "PKMiddHAXo7eQMxJEkDcq7VCnVf",
  "folder_token": "nodcnfKha8zoI7HaoGIBOg7D4Hh"
}
```
