# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → AI 全模态分析视频 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

```
用户粘贴抖音链接 → 提取视频 → FFmpeg抽帧+音频 → AI逐帧分析+语音转文字
→ 综合理解 → 生成脚本JSON → 复制飞书模板 → 填入内容 → 公开链接
```

Web 应用基于 **Streamlit**，部署于 **ModelScope 创空间**（免费，中国大陆可访问，访客免登录）。

---

## 快速开始

### 本地运行
```bash
pip install -r requirements.txt
streamlit run app.py    # 用户界面（端口 8501）
streamlit run admin.py --server.port 8502 --server.headless true   # 管理后台（端口 8502）
```

### ModelScope 创空间部署

1. 在 [ModelScope 创空间](https://www.modelscope.cn/studios) 创建新空间，**接入 SDK 必须选 Streamlit**
2. 在创空间设置 → 环境变量，配置以下密钥（也可跳过，使用内置加密密钥）：
   - `AGNES_API_KEY`（可选，已有内置值）
   - `FEISHU_APP_ID`（可选，已有内置值）
   - `FEISHU_APP_SECRET`（可选，已有内置值）
3. Push 代码到创空间 Git 仓库
4. 重启空间

### ⚠️ 部署检查清单
- [ ] 创空间设置 → 接入 SDK = **Streamlit**（不是 Gradio！）
- [ ] `README.md` 头部 YAML 包含 `sdk: streamlit`
- [ ] `app.py` 的 `__main__` 块不会 `sys.exit(0)`
- [ ] `requirements.txt` 包含 `imageio-ffmpeg>=0.5.0`

---

## 项目结构

```
├── app.py                      # Streamlit 主入口（用户界面 + 流程编排）
├── admin.py                    # 管理后台（自然语言修改配置）
├── config.py                   # 全局配置、API 密钥、Prompt 构建、质量预设、FFmpeg 检测
├── src/                        # 核心模块
│   ├── __init__.py
│   ├── douyin_extractor.py     # 抖音视频链接解析 + 下载
│   ├── video_analyzer.py       # FFmpeg 抽帧 + AI vision 分析 + faster-whisper 转录
│   ├── script_generator.py     # AI 脚本生成（混剪/口播）
│   ├── feishu_ops.py           # 飞书 API 客户端（认证、复制、填充、权限）
│   └── session_manager.py      # 磁盘 checkpoint 持久化
├── config/                     # 配置文件
│   └── requirements.json       # 脚本内容/输出要求的可配置规则
├── requirements.txt            # Python 依赖
├── .streamlit/                 # Streamlit 配置
│   ├── config.toml             # UI 配置
│   └── secrets.toml            # 本地密钥（gitignore）
├── .gitignore
└── README.md
```

---

## 架构与数据流

```
app.py (UI层)
  ├─ 输入：视频URL + 脚本类型 + 质量 + 自定义要求
  ├─ Step1: douyin_extractor → 下载视频
  ├─ Step2: video_analyzer → 逐帧分析 + 音频转录 + 综合报告
  ├─ Step3: script_generator → 生成结构化脚本 JSON
  ├─ Step4: feishu_ops → 复制飞书模板 + 填入内容 → doc_url
  └─ 输出：飞书文档链接
```

### 各模块职责

| 模块 | 职责 |
|------|------|
| `app.py` | Streamlit UI、session state、流程编排、文档生命周期管理 |
| `admin.py` | 独立管理后台，自然语言修改 requirements.json |
| `config.py` | 配置、加密密钥、Prompt 构建、质量预设、FFmpeg 检测 |
| `douyin_extractor.py` | 解析抖音链接 → 下载视频，支持从分享文本中提取 URL |
| `video_analyzer.py` | FFmpeg 抽帧 + AI vision + faster-whisper 转录 + 综合分析 |
| `script_generator.py` | AI 生成结构化脚本 JSON，支持多脚本、内容校验、图片识别 |
| `feishu_ops.py` | 飞书 API 全操作：认证、模板复制、权限、内容填充 |

---

## 配置系统

### 三层配置架构

```
requirements.json          ← 出厂默认值
    ↓
admin.py 自然语言修改      ← 管理员用大白话自定义
    ↓
Prompt 构建函数             ← 合并为最终 Prompt
```

### 质量预设

| 级别 | fps | 最大帧 | Worker | 预计耗时 |
|------|-----|--------|--------|---------|
| 快速 | 1/10 | 30 | 4 | ~30秒 |
| 标准 | 1/5 | 60 | 4 | ~1-2分钟 |
| 精细 | 1/2 | 120 | 3 | ~3-5分钟 |

---

## API 密钥管理

密钥以加密形式存储在 `config.py` 中，运行时自动解密。同时也支持环境变量覆盖。

如需更换密钥（如使用自己的 Agnes AI 账号或飞书应用）：
1. 在创空间 / 本地设置环境变量 `AGNES_API_KEY`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`
2. 或在本地创建 `.streamlit/secrets.toml`（已 gitignore）

环境变量的优先级高于内置加密值。

### 依赖的外部服务

| 服务 | 说明 |
|------|------|
| Agnes AI (`apihub.agnes-ai.com`) | AI 模型 API（文本 + vision） |
| 飞书开放平台 (`open.feishu.cn`) | 文档创建、模板复制、内容填充 |
| FFmpeg | 视频抽帧和音频提取（自动检测，支持 imageio-ffmpeg 兜底） |

---

## 飞书 API 已知行为

### 可用
- 获取 tenant_access_token、复制文件、设置公开权限
- 读写文档 blocks、插入表格行、更新文本（含黄色高亮）
- 图片上传（需传 parent_node）

### 不可用
- 飞书 docx API 不支持创建 Image Block（block_type=27）
- 替代方案：素材列写文字描述

### 表格 Block 结构（重要）
`table.children` 是扁平列表（row-major）：
```
children[r * C + c] = cell(r, c)   （C = 列数）
```

---

## 脚本格式规范

| 类型 | 表格 | 核心要求 |
|------|------|---------|
| 混剪 | 内容 \| 素材（双栏） | 10-16 行，文案无标点，素材为文件名.jpg+描述 |
| 口播 | 原片文案 \| 口播脚本 \| 图片素材（三栏） | 20 轮 A/B 对话，末尾带【情绪标记】 |

---

## 已知限制

- 飞书 API 不支持插入图片到文档
- 视频分析通过逐帧 vision + 音频转录实现，非原生视频理解
- ModelScope 移动端会显示完整创空间外壳

---

## 维护

- 管理员界面：`streamlit run admin.py --server.port 8502`
- 修改脚本规则：打开管理后台 → 对话描述需求 → 确认保存
- 修改后 push 到创空间仓库使其生效
