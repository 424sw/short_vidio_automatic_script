# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → 下载视频 → 语音转文字 → AI 生成混剪/口播脚本 → AI 审核微调 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

核心目标：**仿写** — 保留原视频的信息密度和节奏，用全新措辞创作新脚本。

部署于 **ModelScope 创空间**。

## 快速开始

```bash
pip install -r requirements.txt
python tools/setup_models.py   # 一次性，~462MB
streamlit run app.py           # http://localhost:8501
```

## 项目结构

```
├── app.py                         ←  Streamlit 入口：5 步管道
├── src/
│   ├── douyin_extractor.py        ←  ① 提取：URL 解析 → 下载视频
│   ├── video_analyzer.py          ←  ② 分析：音频提取 + Whisper 转录
│   ├── prompt_builder.py          ←  Prompt 模板：混剪/口播
│   ├── script_generator.py        ←  ③ 生成 + ④ 审核
│   └── feishu_ops.py              ←  ⑤ 飞书：模板复制 → 填充 → 公开
├── config/
│   ├── __init__.py                ←  密钥、API 端点、边界常量、配置加载
│   └── requirements.json          ←  脚本规则配置（修改后重启生效）
├── tools/
│   ├── setup_models.py            ←  faster-whisper 模型下载
│   └── models/faster-whisper-small/  ←  本地 Whisper 模型
├── requirements.txt
└── packages.txt                   ←  apt-get: ffmpeg
```

## 项目边界

**只做两种脚本类型，不会有第三种：**

| 类型 | 形式 | 输出 |
|------|------|------|
| **混剪** (`mix`) | 单人图文讲解 | 文案 + 配图素材 |
| **口播** (`oral`) | A/B 双人角色对话 | 对话 + 原片还原文案 |

用户手动选择（`selectbox`: `["mix", "oral"]`）。

## 管道 5 步

### 步骤 1：提取视频

解析抖音链接（支持短链接跟踪、分享文本自动提取 URL）→ 获取视频信息和下载直链（去水印）→ **FFmpeg 下载**（自动处理 m3u8 分片流和格式兼容问题）→ 保存到 `data/<session_id>/downloads/`

返回：`{video_path, title, author}`

### 步骤 2：语音转文字

1. **提取音频**：FFmpeg 提取为 16kHz 单声道 mp3
2. **语音转文字**：faster-whisper-small 本地模型，CPU 推理。注入领域上下文（品牌名/求职术语/网络热词约 60 个）减少音近错字。独立线程执行，300s 超时返回部分结果

返回：`{"audio_transcript": str}`。不再进行 AI 综合分析，音频转录直接用于步骤 3 的脚本生成。

**两档质量**（仅影响 Whisper）：

| | 标准 (`standard`) | 精细 (`fine`) |
|---|---|---|
| compute_type | int8 | float32 |
| beam_size | 5 | 10 |
| 耗时 | ~60-120s | ~90-180s |

### 步骤 3：生成脚本

按脚本类型约束篇幅（混剪 300-400 字 / 口播 400-500 字）→ 繁体转简体 → 注入 Prompt 模板（直接基于音频转录仿写）→ 一次 AI 调用生成 JSON → `_parse_json()` 解析（失败自动重试一次）。**不在此处校验**（质量问题留步骤 4 集中修）。

### 步骤 4：审核微调

诊断六维度 — **格式**、**长度**（上下限硬性约束）、**相似度**（字符三元组 Jaccard，>40% 触发降重）、**段数**（混剪，5 句+不通过 + 2/3 句行数量平衡 ≥25% + 2+3 占比 ≥65%）、**AI 味**（检测高频 AI 词汇/否定排比/宣传腔）、**标记分布**（口播，限 2/4 字 + 非法长度检测 + 少数方 ≥25%）→ 分两阶段处理：

- **阶段 1（回退循环）**：格式/长度不合格 → 回退重生成（最多 1 次）
- **阶段 2（AI 微调）**：一次 `micro_adjust()` 修复格式/长度/相似度/AI 味/段数 → 若口播标记非法或失衡，追加 `micro_adjust_markers()`（Phase 1 修非法长度 + Phase 2 多数→少数平衡）→ 微调后重审

程序兜底：`_validate()` 硬性检查（title/hashtags ≥4 不含品牌名、行数/轮数范围、角色名 A/B、末尾【标记】）。失败回退原版 + `_fix_markers()` 程序补标记。

**约束**：`original_text` 不准改，【标记】只调标记之前的正文。

### 步骤 5：飞书文档

认证 → 复制模板 → 设公开权限 → 填充内容（混剪双列表格 / 口播三列表格，【标记】黄色高亮）→ 返回链接。文档 5 分钟后自动删除（写入 `data/.expiry_queue` 被动清理）。95201 重试 1.5s→3s→7s。

**飞书限制**：不支持 Image Block（`block_type=27`），素材列只写文字描述。

## AI 调用

最少 1 次（审核直接通过），最多 3 次（回退 + 微调 + 标记微调）：

| # | 步骤 | 调用 | 输入 | 输出 | 何时触发 |
|---|------|------|------|------|----------|
| 1 | 步骤 3 | `generate()` | 音频转录 + Prompt | 脚本 JSON | 始终 |
| 2 | 步骤 4 | `micro_adjust()` | 脚本 + 诊断报告 | 修正后脚本 | 审核不通过时 |
| 3 | 步骤 4 | `micro_adjust_markers()` | 脚本 | 标记分布修正 | 口播且标记非法长度或 2/4 失衡时 |

`review()` 是纯 Python 程序化检查，不调用 AI。

## 配置系统

**`config/__init__.py`**：API 密钥（环境变量 → `st.secrets` → 内置加密值）、边界常量（`DOC_TTL_SECONDS=300`、`FIXED_TARGET_CHARS_MIX/ORAL`、超时常量等）、`load_requirements()`

**`config/requirements.json`**：脚本规则（行数范围、广告品牌、产品介绍库等），修改后重启生效

## 运行时

- **取消**：进度面板按钮 → `cancel_requested` → `_check_cancel()` 抛异常 → 清理。长阻塞调用（Whisper）无法立即中断
- **心跳**：`data/<sid>/.heartbeat`，每次 rerun 刷新，超 120s 判定浏览器断开
- **过期清理**：`main()` 入口扫描 `data/.expiry_queue`，到期文档调飞书删除 API
- **临时文件**：`data/<session_id>/`，步骤异常/完成统一 `rmtree`

## 部署到 ModelScope

```bash
git checkout -b deploy modelscope/master
git rm -rf .
git checkout main -- .
git commit -m "部署最新代码"
git push modelscope deploy:master
git checkout main && git branch -D deploy
```

## ⚠️ Streamlit 缓存清理

Streamlit 只热重载 `app.py`，`src/` 模块不会自动刷新。代码已用 `importlib.reload()` 化解，但 `.pyc` 缓存偶尔残留。

**症状**：代码改了不生效，报错与实际不符。

**解决办法**：
1. 删 `__pycache__`：`find . -name "__pycache__" -type d -exec rm -rf {} +`
2. 杀进程：`netstat -ano | grep 8501` 找到 PID → `taskkill //F //PID <PID>`
3. 等端口释放后重启：`streamlit run app.py --server.port 8501`
4. 不需要检查是否重启成功！

## 后续规划

1. **飞书图片插入** — API 不支持 `block_type=27`，需另辟蹊径
2. **管理界面/SubAgent** — 在原有用户界面基础上增加管理后台
3. **其他优化** — 输出面板多脚本合并到一个文件夹、输入面板支持个性要求（在精细模式中实现）、选项框禁止手动输入、手机端 UI 优化
