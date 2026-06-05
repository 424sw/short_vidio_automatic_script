# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → AI 全模态分析视频 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接，部署于 **ModelScope 创空间**。

## 快速开始

```bash
pip install -r requirements.txt
streamlit run app.py
# 访问 http://localhost:8501
```

`app.py` 是唯一入口，包含用户界面和管理后台。

---

## 项目结构

```
├── app.py                      ← 唯一入口：用户界面 + 管理后台
├── config.py                   ← 配置中心（密钥、API端点、Prompt工厂、质量预设）
├── requirements.txt            ← pip 依赖
├── packages.txt                ← 系统依赖（ModelScope部署用）
├── README.md
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
│       └── code-modification-guard/SKILL.md
└── src/
    ├── __init__.py
    ├── douyin_extractor.py     ← 抖音链接解析 + 视频下载（支持从分享文本提取URL）
    ├── video_analyzer.py       ← FFmpeg抽帧 + AI vision + faster-whisper转录 + 综合
    ├── script_generator.py     ← AI脚本生成 + 结构/内容校验 + 多脚本多样性控制
    ├── feishu_ops.py           ← 飞书API：认证、模板复制、权限、Block操作、内容填充
    └── session_manager.py      ← SHA256(url)→session key, checkpoint持久化, 24h过期清理
```

---

## 架构与数据流

```
app.py (唯一入口)
  ├─ 用户界面：粘贴链接 → 选择参数 → Step1~4
  ├─ 管理后台：头部「管理」→ 密码登录 → 自然语言对话 → AI修改 requirements.json
  └─ 管道：
      Step1 → douyin_extractor.extract()      → video_path, title
      Step2 → video_analyzer.analyze()         → synthesis, transcript
      Step3 → script_generator.generate()      → script JSON (+ hashtags)
      Step4 → feishu_ops.create_and_fill()     → doc_url
```

---

## UI 布局

### 用户界面

```
标题：短视频脚本生成系统
副标题：粘贴抖音链接 → AI 分析 → 输出飞书文档
「管理」expander（密码 + 进入后台 + 忘记密码占位）
────────── 分割线 ──────────
settings_placeholder（st.empty）：
  → step=0 时：输入面板（URL、类型、质量、数量、自定义要求expander、生成按钮）
  → 其他 step：empty() 清空，防 ghost UI
screen（st.empty）：
  → 进度面板 / 结果面板
```

### 管理界面

```
标题：管理控制台
副标题：修改脚本规则 · 管理模板配置 · AI 对话编辑
「← 返回用户界面」全宽按钮
admin_top（st.empty）：
  ├─「查看可管理的内容类型」expander → HTML 表格
  └─「修改管理密码」expander → 三列密码输入 + 按钮靠右
────────── 分割线 ──────────
中部输入区：文字描述 → chat_input + 上传图片 → file_uploader
────────── 分割线 ──────────
底部对话区：AI 对话历史 + 变更确认/撤销
```

### 防 Ghost UI（双锚点 + 过渡帧）

- `settings_placeholder = st.empty()` — 输入面板 anchor，非 step=0 时 `.empty()` 清除
- `screen = st.empty()` — 进度/结果 anchor，每次 `.container()` 完全替换
- 点击"开始生成脚本"后 step 直接从 0 → 1，过渡帧已移除（直接进入进度面板）

### 页面偏移防护

所有 expander 包在 `st.empty()` 容器内，配合 `html { overflow-y: scroll !important; scrollbar-gutter: stable; }` — 滚动条始终预留空间，展开/收起不会改变页面宽度。

### 控件对齐规则

用户界面和管理界面的标签-控件对使用一致模式（`<p>` 标签 + 控件 label_visibility="collapsed"）：

```python
st.markdown('<p style="font-size:0.875rem; margin:0 0 0.25rem 0;">文字描述</p>', unsafe_allow_html=True)
st.text_area("", label_visibility="collapsed", ...)

st.markdown('<p style="font-size:0.875rem; margin:0 0 0.25rem 0;">上传图片</p>', unsafe_allow_html=True)
st.file_uploader("", label_visibility="collapsed", ...)
```

---

## 各模块职责

| 模块 | 职责 |
|------|------|
| `app.py` | Streamlit UI、session state、4步管道、管理后台嵌入、双锚点防ghost UI、进度分步展示 |
| `config.py` | AES加密密钥、API端点、Prompt工厂、质量预设、`get_ffmpeg_path()`、热加载飞书资源ID |
| `douyin_extractor.py` | 正则从分享文本提取URL（多格式）、短链跟踪、视频ID解析、FFmpeg/requests双通道下载 |
| `video_analyzer.py` | FFmpeg抽帧(按质量级fps)、音频提取、faster-whisper转录、并发AI vision逐帧分析、综合报告 |
| `script_generator.py` | AI生成结构化JSON（含hashtags）、结构/内容双重校验+重试反馈、多脚本trigram去重、图片要求提取、类型自动检测 |
| `feishu_ops.py` | OAuth认证、模板复制、公开权限、Block get/update/insert_row、表格行列式填充、交付字段替换、文档删除 |
| `session_manager.py` | SHA256(url)→session key、state.json磁盘checkpoint、24h过期清理 |

---

## 配置系统（两层 + 热加载）

```
requirements.json          ← 出厂默认值 + 管理员对话修改
    ↓
app.py 管理后台            ← 头部「管理」→ 密码登录 → 自然语言对话 → 确认保存
    ↓
config.py Prompt构建函数    ← 合并默认值 → 最终 Prompt
```

管理员保存后无需重启 Streamlit 即可生效。

---

## 输出质量预设

质量控制三个维度：`fps`（每秒抽帧数）、`max_frames`（最大帧数）、`workers`（并发数）。

| 质量 | fps | max_frames | workers | 预计耗时 |
|------|-----|-----------|---------|---------|
| 🚀 快速 | 1 | 60 | 4 | 约 1-2 分钟 |
| ⚖️ 标准 | 2 | 150 | 4 | 约 3-5 分钟 |
| 🎯 精细 | 3 | 300 | 3 | 约 6-10 分钟 |

进度面板中每步的预计时间也按质量分级（`_STEP_TIMES`），确保单步最大预估 ≤ 总预计。

---

## 管理员密码

- **默认密码**：`admin888`
- **修改密码**：管理后台「修改管理密码」expander
- **持久化**：`save_admin_credentials()` → `config/admin.json`
- **紧急恢复**：删除 `config/admin.json` 即恢复默认密码

---

## 飞书 API 已知行为

- **可用**：获取token、复制文件、设置权限、读写blocks、插入表格行、更新文本（黄色高亮）、上传图片
- **不可用**：创建 Image Block（block_type=27）→ 素材列写文字描述
- **表格结构**：`children` 是扁平 row-major 列表，`children[r*C+c] = cell(r,c)`
- **`background_color`**：只能 1-20，不能为 0。更新文本时先检查原值
- **非 text_run 元素**：重建 elements 列表时必须保留，不能丢弃

---

## 脚本格式

| 类型 | 结构 | 要求 |
|------|------|------|
| 混剪 | title + hashtags + rows(name+material) | 10-16行，文案无标点，素材=文件名.jpg+描述 |
| 口播 | title + hashtags + original_text + dialogs(A+B+情绪) + images | 20轮对话，末尾【情绪标记】 |

广告品牌"鱼泡直聘"后紧跟的产品介绍从「产品介绍库」中由 AI 自动匹配。

---

## CSS 注入规则

位置：`main()` 函数开头 `st.markdown(<style>...)`。

核心规则：

- `html, body, [data-testid="stAppViewContainer"] { overflow-y: scroll; }` + `scrollbar-gutter: stable` — 页面偏移防护
- `[data-testid="stToolbar"] { display: none }` — 隐藏 Streamlit 工具栏
- `footer { display: none }` — 隐藏页脚
- `.stMarkdown h1 a, h2 a, h3 a { display: none }` — 隐藏标题锚点
- 密码字段 — 隐藏浏览器原生密码眼睛图标
- 文件上传框 — 压缩高度、隐藏尺寸提示、`font-size:0` + `::after` 替换"上传图片"
- `[data-testid="stCodeBlock"] pre, code { white-space: pre-wrap; word-break: break-word; }` — 管理界面长配置行自动换行
- `@media (max-width: 768px)` — 上传框/文件名 `nowrap + ellipsis`，含通配符后代选择器

---

## 部署

### 双远程

```
origin     → GitHub     (https://github.com/424sw/short_vidio_automatic_script.git)
modelscope → ModelScope (https://www.modelscope.cn/studios/sw4242/short-video-script.git)
```

### 推送到 ModelScope

ModelScope 创空间默认读取 **master** 分支，且受保护（不允许 force push）。

部署方式：从 `modelscope/master` 开临时分支 → 清空工作区 → 从 `main` 还原文件 → 提交 → 推回 `master`：

```bash
git checkout -b deploy modelscope/master
git rm -rf .
git checkout main -- .
git commit -m "部署最新代码"
git push modelscope deploy:master
git checkout main && git branch -D deploy
```

本质：parent = `modelscope/master`，tree = `main`，是合法前向提交，不触发 force push 拦截。

### 双分支都推（保险）：

```bash
git push modelscope main:main
```

---

## 已知待优化问题

1. **Ghost UI**：双锚点基本解决，但 Streamlit 框架无法 100% 根除
2. **飞书模板结构依赖**：`feishu_ops.py` 的 `_fill_*` 方法假设固定 block 索引，手动改模板需同步更新
3. **忘记密码功能**：占位状态，紧急恢复需手动删除 `config/admin.json`
4. **移动端适配**：上传框换行已解决，更完善适配需全量测试
