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

**app.py 是唯一入口**，包含用户界面和管理后台。管理后台通过侧边栏「⚙️ 管理」展开 → 输入密码进入。管理密码加密存储在 `config.py` 中。

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
│   └── config.toml             ← UI 主题
├── config/
│   └── requirements.json       ← 脚本规则 + 模板配置 + 交付要求（管理员对话修改）
└── src/
    ├── __init__.py
    ├── douyin_extractor.py     ← 抖音链接解析 + 视频下载（支持从文本提取URL）
    ├── video_analyzer.py       ← FFmpeg抽帧 + AI vision + faster-whisper转录 + 综合
    ├── script_generator.py     ← AI脚本生成 + 结构/内容校验 + 多脚本 + 图片识别
    ├── feishu_ops.py           ← 飞书API：认证、模板复制、权限、内容填充、删除
    └── session_manager.py      ← 磁盘checkpoint持久化 + 过期清理
```

本地存在的 `.streamlit/credentials.toml`、`.streamlit/secrets.toml`、`data/`、`.claude/`、`.mcp.json` 均不在仓库中。

---

## 架构与数据流

```
app.py (唯一入口)
  ├─ 用户界面：粘贴链接 → 选择参数 → Step1~4
  ├─ 管理后台：侧栏密码 → 对话修改 requirements.json
  └─ 管道：
      Step1 → douyin_extractor.extract()      → video_path, title
      Step2 → video_analyzer.analyze()         → synthesis, transcript
      Step3 → script_generator.generate()      → script JSON (+ hashtags)
      Step4 → feishu_ops.create_and_fill()     → doc_url
```

---

## 各模块职责

| 模块 | 职责 |
|------|------|
| `app.py` | Streamlit UI、session state、三步面板替换、4步管道、文档生命周期（5min TTL）、管理后台嵌入 |
| `config.py` | 密钥（XOR+SHA256加密存储）、API端点、Prompt构建函数、质量预设、FFmpeg检测 |
| `douyin_extractor.py` | 正则提取URL（支持分享文本）、下载视频 |
| `video_analyzer.py` | FFmpeg抽帧、AI vision逐帧描述、faster-whisper语音转录、综合报告 |
| `script_generator.py` | AI生成结构化JSON（含hashtags）、内容校验+重试反馈、多脚本多样性控制、图片要求提取 |
| `feishu_ops.py` | 飞书API：OAuth认证、模板复制、Block操作（get/update/insert_row）、权限设置、交付要求字段填充 |
| `session_manager.py` | SHA256(url)→session key、state.json checkpoint、24h过期清理 |

---

## 配置系统（两层）

```
requirements.json          ← 出厂默认值 + 管理员对话修改
    ↓
[app.py 管理后台]          ← 管理员侧栏输入密码 → 自然语言对话 → 确认保存 → 修改 requirements.json
    ↓
config.py Prompt构建函数    ← 合并默认值 → 最终 Prompt
```

---

## 密钥管理

API 密钥（Agnes API Key、飞书 App ID/Secret、管理密码）使用 XOR+SHA256+Base64 加密存储在 `config.py` 中，运行时自动解密。支持环境变量覆盖（优先级：环境变量 > streamlit secrets > 内置加密值）。

---

## 飞书 API 已知行为

- **可用**：获取token、复制文件、设置权限、读写blocks、插入表格行、更新文本（黄色高亮）、上传图片
- **不可用**：创建 Image Block（block_type=27）→ 素材列写文字描述
- **表格结构**：`children` 是扁平列表（row-major），`children[r*C+c] = cell(r,c)`
- **模板ID和文件夹Token** 存储在 `requirements.json` 的「模板配置」节

---

## 脚本格式

| 类型 | 结构 | 要求 |
|------|------|------|
| 混剪 | title + hashtags + rows(name+material) | 10-16行，文案无标点，素材=文件名.jpg+描述 |
| 口播 | title + hashtags + original_text + dialogs(A+B+情绪) + images | 20轮对话，末尾【情绪标记】 |
