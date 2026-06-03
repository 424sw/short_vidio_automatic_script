# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → AI 全模态分析视频 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

```
用户粘贴抖音链接 → 提取视频 → FFmpeg抽帧+音频 → AI逐帧分析+语音转文字
→ 综合理解 → 生成脚本JSON → 复制飞书模板 → 填入内容 → 公开链接
```

Web 应用基于 **Streamlit**，部署于 **ModelScope 创空间**。

---

## 快速开始

```bash
pip install -r requirements.txt
streamlit run app.py
# 访问 http://localhost:8501
```

**app.py 是唯一入口**，包含用户界面和管理后台。

---

## 项目结构（GitHub 仓库）

```
├── app.py                      ← 唯一入口：用户界面 + 管理后台
├── config.py                   ← 配置中心（加密密钥、API端点、Prompt工厂、质量预设）
├── requirements.txt            ← pip 依赖
├── packages.txt                ← 系统依赖（ModelScope部署用）
├── README.md                   ← 部署指引
├── CLAUDE.md                   ← 本文件
├── .gitignore
├── .streamlit/
│   └── config.toml             ← UI 主题（toolbarMode=viewer）
├── config/
│   ├── requirements.json       ← 脚本规则 + 模板配置 + 产品介绍库 + 交付要求
│   └── admin.json              ← 管理员密码加密存储（首次使用前不存在）
├── data/                       ← 运行时数据（sessions/、downloads/、doc_registry.json）
├── .claude/
│   ├── settings.json
│   └── skills/
│       └── code-modification-guard/SKILL.md  ← 代码修改安全守则
└── src/
    ├── __init__.py
    ├── douyin_extractor.py     ← 抖音链接解析 + 视频下载（支持从文本提取URL）
    ├── video_analyzer.py       ← FFmpeg抽帧 + AI vision + faster-whisper转录 + 综合
    ├── script_generator.py     ← AI脚本生成 + 结构/内容校验 + 多脚本 + 图片识别
    ├── feishu_ops.py           ← 飞书API：认证、模板复制、权限、内容填充、删除
    └── session_manager.py      ← 磁盘checkpoint持久化 + 过期清理
```

---

## 架构与数据流

```
app.py (唯一入口)
  ├─ 用户界面：粘贴链接 → 选择参数 → Step1~4
  ├─ 管理后台：头部「管理」入口 → 密码登录 → 对话修改 requirements.json
  └─ 管道：
      Step1 → douyin_extractor.extract()      → video_path, title
      Step2 → video_analyzer.analyze()         → synthesis, transcript
      Step3 → script_generator.generate()      → script JSON (+ hashtags)
      Step4 → feishu_ops.create_and_fill()     → doc_url
```

---

## UI 布局详细说明

### 用户界面布局

```
标题：短视频脚本生成系统
副标题：粘贴抖音链接 → AI 分析 → 输出飞书文档
「管理」expander（密码 + 进入后台 + 忘记密码占位）
────────── 分割线 ──────────
settings_placeholder（st.empty）：
  → step=0 时：输入面板（URL、类型、质量、数量、自定义要求、生成按钮）
  → 其他 step：empty() 清空，防止 ghost UI
screen（st.empty）：
  → 过渡帧 / 进度面板 / 结果面板
```

### 管理界面布局

```
标题：管理控制台
副标题：修改脚本规则 · 管理模板配置 · AI 对话编辑
「← 返回用户界面」全宽按钮
admin_top（st.empty）：
  ├─「查看可管理的内容类型」expander → HTML 表格
  └─「修改管理密码」expander → 三列密码输入 + 按钮靠右
────────── 分割线 ──────────
中部输入区：文字描述 → 对话框 + 上传图片 → 文件上传组件
────────── 分割线 ──────────
底部对话区：AI 对话历史 + 变更确认/撤销
```

### 防 Ghost UI 机制（两个 st.empty 锚点）

1. **`settings_placeholder`**：输入面板的独立 anchor，位于分割线下方。step=0 时渲染 `render_input_panel()`，非 step=0 时调用 `.empty()` 强制清除所有输入 widgets。
2. **`screen`**：进度/结果的独立 anchor，每次 `screen.container()` 完全替换内容。
3. **过渡帧**（`_transition_step`）：点击"开始生成脚本"后插入一帧干净的「正在准备生成…」页面，同时显式调用 `settings_placeholder.empty()`，彻底清除旧 widgets 后再进入 step=1。

**关键代码结构**（`main()` 函数中）：

```python
# 用户模式 — 分割线上方
标题 HTML
「管理」expander → user_header.st.empty().container()
st.divider()

# 分割线下方 — 双锚点
settings_placeholder = st.empty()   # 输入面板
screen = st.empty()                 # 进度/结果
if not admin and step==0 and _transition_step==0:
    settings_placeholder.container() → render_input_panel()
else:
    settings_placeholder.empty()

with screen.container():
    if _transition_step: settings_placeholder.empty(); 过渡帧; st.rerun()
    elif step==0: check_recovery()
    elif step>=1: 进度/结果...
```

### 管理界面防偏移机制

管理界面的 expander（`"查看可管理的内容类型"`、`"修改管理密码"`）包裹在 `admin_top = st.empty()` + `with admin_top.container():` 内；用户界面的 `"管理"` expander 包裹在 `user_header = st.empty()` + `with user_header.container():` 内。

**设计原则**：所有可能因展开/收起而改变页面高度的 expander 都应该放在 `st.empty()` 容器中，与 `html { overflow-y: scroll }` 配合使用，防止页面宽度跳动。

---

## 文件上传组件统一写法

所有 `st.file_uploader` 使用以下模式（label 隐藏 + CSS 伪元素显示文字）：

```python
st.file_uploader(
    "",
    type=["png", "jpg", "jpeg", "webp"],
    key="...",
    label_visibility="collapsed",  # 隐藏 label，文字由 CSS ::after 提供
)
```

对应 CSS：
```css
[data-testid="stFileUploader"] span[data-testid="stFileUploaderDropzoneText"] {
    font-size: 0 !important;
}
[data-testid="stFileUploader"] span[data-testid="stFileUploaderDropzoneText"]::after {
    content: "上传图片" !important;
    font-size: 0.88rem !important;
}
```

这样做的好处：管理界面和用户界面的上传框外观完全统一，不受 Streamlit 默认 label 文字长度影响。

---

## 各模块职责

| 模块 | 职责 |
|------|------|
| `app.py` | Streamlit UI、session state、4步管道、文档生命周期（5min TTL）、管理后台嵌入、双锚点防ghost UI |
| `config.py` | 密钥（XOR+SHA256加密存储）、API端点、Prompt构建函数、质量预设、FFmpeg检测、热加载飞书资源ID |
| `douyin_extractor.py` | 正则提取URL（支持从分享文本中提取）、下载视频 |
| `video_analyzer.py` | FFmpeg抽帧、AI vision逐帧描述、faster-whisper语音转录、综合报告 |
| `script_generator.py` | AI生成结构化JSON（含hashtags）、内容校验+重试反馈、多脚本多样性控制、图片要求提取 |
| `feishu_ops.py` | 飞书API：OAuth认证、模板复制、Block操作（get/update/insert_row）、权限设置、交付要求字段填充 |
| `session_manager.py` | SHA256(url)→session key、state.json checkpoint、24h过期清理 |

---

## 配置系统（两层 + 热加载）

```
requirements.json          ← 出厂默认值 + 管理员对话修改
    ↓
[app.py 管理后台]          ← 头部「管理」→ 密码登录 → 自然语言对话 → 确认保存
    ↓
config.py Prompt构建函数    ← 合并默认值 → 最终 Prompt
```

**管理员配置即时生效**：除了 Prompt 构建函数每次从磁盘读取 `requirements.json`，飞书资源 ID 也使用函数动态读取：
- `get_folder_token()` — 每次调用从磁盘读取文件夹 Token
- `get_template_id(t)` — 每次调用从磁盘读取模板 ID
- `load_requirements()` — 从磁盘读取完整配置内容规则

管理员保存后无需重启 Streamlit 即可生效。

---

## 密钥管理

API 密钥（Agnes API Key、飞书 App ID/Secret、管理密码）使用 XOR+SHA256+Base64 加密存储在 `config.py` 中，运行时自动解密。支持环境变量覆盖（优先级：环境变量 > streamlit secrets > 内置加密值）。

### 管理员密码

- **默认密码**：`admin888`（硬编码在 `config.py` 中，通过 XOR 解密得到）
- **修改密码**：管理后台「修改管理密码」expander → 输入新旧密码 → 生成新的恢复密钥
- **密码持久化**：`save_admin_credentials(password, recovery_key)` 写入 `config/admin.json`
- **首次使用**：`admin.json` 不存在时使用默认密码。首次修改密码后自动创建 `admin.json`
- **忘记密码**：当前为占位状态（提示"开发中"）。紧急恢复：删除 `config/admin.json` 文件即恢复默认密码

---

## 飞书 API 已知行为

- **可用**：获取token、复制文件、设置权限、读写blocks、插入表格行、更新文本（黄色高亮）、上传图片
- **不可用**：创建 Image Block（block_type=27）→ 素材列写文字描述
- **表格结构**：`children` 是扁平列表（row-major），`children[r*C+c] = cell(r,c)`
- **`background_color` 字段**：只能为 1-20，不能为 0。更新文本元素时必须先检查原值，仅当存在且非零时才带入
- **非 text_run 元素**（如 mention_user）：重建元素列表时必须保留，不能丢弃
- **模板ID和文件夹Token** 存储在 `requirements.json` 的「模板配置」节，通过 `get_folder_token()` / `get_template_id()` 动态读取

---

## 脚本格式

| 类型 | 结构 | 要求 |
|------|------|------|
| 混剪 | title + hashtags + rows(name+material) | 10-16行，文案无标点，素材=文件名.jpg+描述 |
| 口播 | title + hashtags + original_text + dialogs(A+B+情绪) + images | 20轮对话，末尾【情绪标记】 |

---

## requirements.json 结构

```json
{
  "模板配置": {
    "文件夹Token": "...",
    "混剪模板ID": "...",
    "口播模板ID": "...",
    "产品介绍库链接": "飞书wiki链接"
  },
  "通用": { 语言、返回格式 },
  "混剪": { 标题字数、行数范围、文案风格、素材格式、广告配置 },
  "口播": { 标题字数、对话轮数、角色格式、情绪选项、图片素材、对话结构 },
  "交付要求": { 话题词、标题占位、正文占位、发布状态 },
  "产品介绍库": [ 五个主题的产品文案 ]
}
```

广告品牌"鱼泡直聘"后紧跟的产品介绍从「产品介绍库」中由 AI 根据视频内容自动匹配。

---

## CSS 注入规则

位置：`main()` 函数开头，`st.markdown(<style>...)`。

```css
html { overflow-y: scroll !important; }                           /* 防止 expander 展开时页面偏移 */
[data-testid="stToolbar"] { display: none !important; }           /* 隐藏 Streamlit 工具栏 */
footer { display: none !important; }                              /* 隐藏页脚 */
.stMarkdown h1 a, .stMarkdown h2 a, .stMarkdown h3 a { display: none; }  /* 隐藏标题锚点 */
input[type="password"]::-ms-reveal, ::-ms-clear { display: none; }  /* 隐藏浏览器密码眼睛 */
[data-testid="stFileUploader"] section { ... }                    /* 上传框高度与对话框对齐 */
[data-testid="stFileUploaderDropzone"] small { display: none; }   /* 隐藏上传框尺寸提示 */
[data-testid="stFileUploaderDropzone"] { min-height: 0; }         /* 压缩上传框 */
@media (max-width: 768px) { ... }                                 /* 移动端上传框内容不换行 */
```

---

## 已知待优化问题

1. **Ghost UI**：双锚点架构（settings_placeholder + screen）已大幅缓解。过渡帧覆盖 step 0→1 的切换。Streamlit 框架层面的限制，无法 100% 根除。
2. **Expander 页面抖动**：目前 `html { overflow-y: scroll }` + `st.empty()` 容器已解决大部分情况，但在某些 Streamlit 版本下仍有概率出现。`scrollbar-gutter: stable` 是更优雅的方案但兼容性有限。
3. **视频分析速度**：取决于视频长度和 quality 预设。fast 模式约 1 分钟，fine 模式可达 5 分钟。
4. **飞书模板结构依赖**：代码硬编码了模板的 block 索引，如果模板被手动修改，`feishu_ops.py` 中的 `_fill_*` 方法需要同步更新。
5. **忘记密码功能**：当前为占位状态，计划后续通过手机号验证码实现。
6. **文件上传框高度对齐**：管理界面已通过 CSS 压缩实现，但 Streamlit 原生组件高度差异较大，在不同浏览器可能有微小的像素偏差。
7. **移动端适配**：上传框描述文字在移动端的换行问题已通过 `@media (max-width: 768px)` + `white-space: nowrap` 部分解决，但更完善的移动端适配需要全面测试。

---

## app.py 顶层 import 清单

当前 `app.py` 从 `config.py` 导入的符号：

```python
from config import (
    QUALITY_PRESETS, get_quality_config, generate_doc_title,
    AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL, ADMIN_PASSWORD,
    save_admin_credentials,  # ← 用于管理面板修改密码
)
```

注意：`ADMIN_RECOVERY_KEY` 已不再导入（忘记密码功能已简化，后续重构时再接入）。
