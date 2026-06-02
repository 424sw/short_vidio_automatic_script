# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → AI 全模态分析视频 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

```
用户粘贴抖音链接 → 提取视频 → FFmpeg抽帧+音频 → AI逐帧分析+语音转文字
→ 综合理解 → 生成脚本JSON → 复制飞书模板 → 填入内容 → 公开链接
```

Web 应用基于 **Streamlit**，支持本地运行和 Streamlit Cloud 免费部署。

---

## 项目结构

```
short_vidio_automatic_script/
├── app.py                      # Streamlit 主入口（UI + 流程编排）
├── config.py                   # 全局配置、API密钥、Prompt构建、质量预设
├── src/                        # 核心模块
│   ├── __init__.py
│   ├── douyin_extractor.py     # 抖音视频链接解析 + 下载
│   ├── video_analyzer.py       # FFmpeg抽帧 + AI逐帧分析 + faster-whisper转录
│   ├── script_generator.py     # AI脚本生成（混剪/口播）
│   └── feishu_ops.py           # 飞书API客户端（认证、复制、填充、权限、图片上传）
├── config/                     # 配置文件
│   └── requirements.json       # 脚本内容/输出要求的可配置规则
├── requirements.txt            # Python依赖
├── packages.txt                # Streamlit Cloud 系统依赖（ffmpeg）
├── .streamlit/                 # Streamlit 配置
│   ├── config.toml             # UI 配置（隐藏工具栏）
│   └── secrets.toml            # 本地密钥（gitignore）
├── .mcp.json                   # Claude Code MCP 配置（开发用）
├── .gitignore
└── CLAUDE.md                   # 本文件
```

---

## 架构与数据流

```
app.py (UI层)
  ├─ 输入：视频URL + 脚本类型(auto/mix/oral) + 质量(fast/standard/fine) + 自定义要求
  ├─ Step1: douyin_extractor.extract() → video_path, title, author
  ├─ Step2: video_analyzer.analyze() → frame_analysis[], synthesis, audio_transcript
  ├─ Step3: script_generator.generate() → script JSON
  ├─ Step4: feishu_ops.create_and_fill() → doc_url
  └─ 输出：飞书文档链接 + 创建副本提示
```

### 各模块职责

| 模块 | 职责 | 对外接口 |
|------|------|---------|
| `app.py` | Streamlit UI、session state、4步管道 | `main()` |
| `config.py` | 配置、密钥、Prompt构建、质量预设、要求加载 | `load_requirements()`, `build_mix_prompt()`, `build_oral_prompt()`, `get_quality_config()` |
| `douyin_extractor.py` | 解析抖音链接 → 下载视频 | `DouyinExtractor.extract(url, dir)` → `{video_path, title, author, video_id}` |
| `video_analyzer.py` | FFmpeg抽帧 + AI vision + faster-whisper转录 + 综合 | `VideoAnalyzer.analyze(path, title, author, quality)` → `{frame_analysis, synthesis, audio_transcript}` |
| `script_generator.py` | 调用AI生成结构化脚本JSON | `ScriptGenerator.generate(synthesis, title, type, custom_req)` → `dict` |
| `feishu_ops.py` | 飞书API全操作 | `FeishuClient.create_and_fill(type, script, url, title)` → `{doc_id, url}` |

---

## 配置系统

### 三层配置架构

```
requirements.json          ← 出厂默认值（部署时自带）
    ↓
网页内编辑器（session）    ← 用户用大白话自定义，覆盖默认规则
    ↓
Prompt 构建函数             ← 将默认+自定义合并为最终 Prompt
```

### 质量预设（`QUALITY_PRESETS` in config.py）

| 级别 | fps | 最大帧 | Worker | 预计耗时 |
|------|-----|--------|--------|---------|
| 🚀 快速 | 1/10 | 30 | 4 | ~30秒 |
| ⚖️ 标准 | 1/5 | 60 | 4 | ~1-2分钟 |
| 🎯 精细 | 1/2 | 120 | 3 | ~3-5分钟 |

### 用户自定义要求

用户在网页展开"✏️ 自定义要求"，用大白话描述需求（如"短一点6-8行，不要广告"），AI 将其与默认规则合并，冲突时以用户要求为最高优先级。无需懂 JSON 或编程。

---

## 关键凭证与端点

### AI API (Agnes)
| 项 | 值 |
|----|-----|
| Base URL | `https://apihub.agnes-ai.com/v1` |
| 模型 | `agnes-2.0-flash`（文本 + vision） |
| API Key | `sk-ZhwA3nuflKAXF2KkkcBJFj1oJwUk5GnyOoMTk2xkudKhX9L9` |
| SDK | `openai.OpenAI(base_url=..., api_key=...)` |

### 飞书 API
| 项 | 值 |
|----|-----|
| Auth URL | `POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal` |
| Base URL | `https://open.feishu.cn/open-apis` |
| App ID | `cli_aa97347bb5f9dbd7` |
| App Secret | `UnGjpZgesVm4e0OKKkX5AEARIKiji4RC` |
| Token有效期 | 2小时（代码自动提前5分钟刷新） |
| 文件夹 Token | `nodcnfKha8zoI7HaoGIBOg7D4Hh` |

### 飞书文档 ID 映射

| 文档 | node_token | obj_token (doc_id) | 用途 |
|------|-----------|---------------------|------|
| 混剪模板 | `MnKRwLm1dihCpCk8QpAcivZZnRz` | `B1HtdfhjKo4g4QxgNNncCtVwnth` | 复制生成混剪副本 |
| 口播模板 | `SXcLwR1maiy97VkTVihcdSann3e` | `EbLGdZ2qYoQgpixsmQjc5EkjnNf` | 复制生成口播副本 |

### 其他
| 项 | 值 |
|----|-----|
| FFmpeg | 自动检测 PATH → Windows 已知路径 → 回退 `"ffmpeg"` |
| iPhone UA | `Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 ...)` |
| 重试 | 最多3次，退避系数 0.5s |

---

## 飞书 API 已验证行为

### ✅ 可用

| API | 端点 | 要点 |
|-----|------|------|
| 获取 token | `POST /auth/v3/tenant_access_token/internal` | 2小时有效 |
| 复制文件 | `POST /drive/v1/files/{id}/copy` | 需 `name`, `type: "docx"`, `folder_token` |
| 设置公开权限 | `PATCH /drive/v1/permissions/{id}/public` | `anyone_editable` |
| 获取 blocks | `GET /docx/v1/documents/{id}/blocks` | `page_size=500` |
| 更新文本 | `PATCH /docx/v1/documents/{id}/blocks/{block_id}` | 支持 `background_color: 3`（黄色高亮） |
| 插入表格行 | `PATCH /blocks/batch_update` → `insert_table_row` | 行索引随插入递增 |
| 写入嵌入表格 | `PUT /sheets/v2/spreadsheets/{token}/values` | 口播标题写入 A2 |
| 上传图片 | `POST /drive/v1/medias/upload_all` | **必须传 `parent_node`**（文档block_id） |

### ❌ 不可用

| API | 错误码 | 说明 |
|-----|--------|------|
| `descendant`/`children` 创建 Image Block | `1770001` | 飞书 docx API 不支持创建 block_type=27 |
| `batch_update` 替换/新增 Image Block | `1770001` | 同上 |
| `update_text_elements` `inline_file` | 静默忽略 | API返回200但实际不生效 |

**结论**：图片可上传到飞书服务器，但无法通过 API 插入文档。当前方案为素材列写文字描述。

### 表格 Block 结构（重要）

`table.children` 是**扁平列表**（row-major），不是嵌套的 row → cell 结构：
```
children[0] = cell(0,0), children[1] = cell(0,1)
children[2] = cell(1,0), children[3] = cell(1,1)
...
children[r * C + c] = cell(r, c)   （C = 列数）
```
每个 cell 的 `children[0]` 是其中的文本 block 的 block_id。

### 模板结构

**混剪模板**：Page → 视频链接Callout → 标题 → 空文本 → 图文配置 → Table(10r×2c) → 交付要求(headings, bullets)

**口播模板**：Page → 视频链接Callout → Sheet(嵌入表格A2填标题) → 详情 → Table(2r×3c) → 交付要求

---

## 脚本格式规范

### 混剪脚本
- 双栏表格（内容 | 素材），表头固定
- 内容列：口播文案，不用标点符号，换行分隔
- 素材列：`文件名.jpg 中文描述` 格式
- 行数：10-16行（可配置）
- 广告在前50%位置软广植入

### 口播脚本
- 三栏表格（原片文案 | 正式口播脚本 | 图片素材）
- 正式口播脚本：20轮 A/B 对话，末尾【情绪标记】黄色高亮
- 情绪选项：疑惑/热心/鼓励/发愁/惊讶/推荐/无奈/期待
- 图片素材：emoji开头+描述

### 交付要求模块
- **仅修改**封面要求中的标题占位符（`bg=3` 黄色高亮）
- 其他所有内容保持模板原样

---

## 部署

### 本地运行
```bash
pip install -r requirements.txt
streamlit run app.py
# 访问 http://localhost:8501
```

### Streamlit Cloud 部署
1. Push 代码到 GitHub
2. [Streamlit Cloud](https://streamlit.io/cloud) → New app → 选仓库
3. Secrets 设置：`AGNES_API_KEY`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`
4. `packages.txt` 自动安装 ffmpeg
5. 获取 `https://xxx.streamlit.app` 公开链接

### 部署前清单
- [ ] `.streamlit/secrets.toml` 在 `.gitignore` 中
- [ ] Streamlit Cloud Secrets 已配置
- [ ] `requirements.txt` 依赖完整
- [ ] `packages.txt` 包含 `ffmpeg`

---

## 已知问题 & 后续方向

### 图片插入
飞书 docx API 当前不支持创建 Image Block。所有尝试（descendant、children、batch_update、inline_file）均失败。后续可关注飞书开放平台是否开放此能力。

### 视频分析
- `agnes-video-v2.0` 是视频生成模型，不支持视频理解
- 当前方案：逐帧 vision + 音频转录，效果可接受
- 后续可尝试：更专业的视频理解 API / 多模型融合

### 功能拓展方向
- 话题/关键词输入（无视频时直接生成脚本）
- 本地视频上传
- 批量处理（Excel/CSV 导入）
- 多版本脚本供选择
- PDF/Markdown 导出
- 飞书 OAuth 授权（直接复制到用户账号）
