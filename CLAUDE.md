# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → AI 全模态分析视频 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

部署于 **ModelScope 创空间**。

## 快速开始

```bash
pip install -r requirements.txt
python tools/setup_models.py   # 一次性，~75MB
streamlit run app.py           # http://localhost:8501
```

## 项目结构

```
├── app.py                         ← 🚪 入口：极简 4 步管道
├── src/                           ← ⚙️ 核心引擎
│   ├── douyin_extractor.py        ←   ① 提取：URL → 下载
│   ├── video_analyzer.py          ←   ② 分析：抽帧 + 转录 + AI 综合
│   ├── prompt_builder.py          ←   📝 Prompt：综合分析/混剪/口播
│   ├── script_generator.py        ←   ③ 生成：AI → JSON → 校验
│   └── feishu_ops.py              ←   ④ 飞书：模板复制 → 填充 → 公开
├── config/                        ← ⚙️ 密钥 + requirements.json + admin.json
├── tools/                         ← 🛠️ setup_models.py + faster-whisper 模型
└── _archive/                      ← 🗑️ 旧版完整代码备份
```

## 管道 4 步

| 步骤 | 函数 | 核心文件 |
|------|------|---------|
| 1. 提取视频 | `step1_extract()` | [douyin_extractor.py](src/douyin_extractor.py) |
| 2. AI 分析 | `step2_analyze()` | [video_analyzer.py](src/video_analyzer.py) |
| 3. 生成脚本 | `step3_generate()` | [script_generator.py](src/script_generator.py) |
| 4. 飞书文档 | `step4_feishu()` | [feishu_ops.py](src/feishu_ops.py) |

## 三次 AI 调用

| 调用 | 所在步骤 | 输入 | 输出 | 模型 | 状态 |
|------|---------|------|------|------|------|
| ① 分析参考视频 | 步骤 2 | 抽帧图片 + 音频转录 | 视频结构分析、完整口播文案、风格特征、关键信息点（synthesis） | `AGNES_MODEL` | ✅ 已实现 |
| ② 生成脚本内容 | 步骤 3 | synthesis + Prompt 要求 | 混剪/口播脚本 JSON | `AGNES_MODEL` | ✅ 已实现 |
| ③ 审核微调 | 步骤 3→4 之间 | 已生成脚本 + 原始 Prompt | 校验修正后的脚本 | `AGNES_MODEL` | 📋 规划中 |

### ① 分析参考视频
[VideoAnalyzer.synthesize()](src/video_analyzer.py#L249)：将抽帧描述 + 语音转文字合并，由 AI 输出视频结构、完整口播文案、风格特征、关键信息点。后续可扩展 `detect_type()` 在此步自动判断混剪/口播类型。

### ② 生成脚本内容
[ScriptGenerator.generate()](src/script_generator.py#L22)：将 synthesis 注入混剪/口播 Prompt 模板，AI 输出严格 JSON（title / hashtags / rows 或 dialogs / images / original_text），经 `_validate()` 校验后返回。

### ③ 审核微调（规划中）
在步骤 3 生成完成后、步骤 4 飞书填入前，增加一次 AI 自检：将已生成的脚本 JSON 连带原始 Prompt 回传给 AI，要求逐项对照校验，自动修正格式偏差和内容缺失。

## 当前状态（截至 2026-06-06）

### app.py — 2026-06-06 UI 重构

三面板独立化：`main()` 改为严格 `if/elif/else` 三路分支，面板互不干扰。进度面板重写：去除 `st.status()`（可折叠下拉框），改用 `st.progress()` 进度条 + 步骤标题 + `st.spinner()`。

### 已验证修复 ✅

| 修复内容 | 文件 | 方式 |
|---------|------|------|
| 三面板独立化 | app.py | `main()` 严格 if/elif/else |
| 进度面板重写 | app.py | 去 `st.status`，换进度条 + spinner |
| 表格多行换行 | feishu_ops.py | `multiline=True` 拆分 text_run 已生效 ✅ |
| 软广植入 | prompt_builder.py | 🔴 硬性指令：前50%/第一个品牌/产品库匹配 |
| 话题词校验 | script_generator.py `_validate` | hashtags 不足 3 个抛 ScriptGeneratorError |
| rows 跨列解包 | feishu_ops.py `_fill_mix_table` | `for i, row in enumerate(rows_data)` + 索引取值 |
| 标题含话题词 | feishu_ops.py `fill_mix_script` | `mix_title = title + " " + " ".join(f"#{t}" for t in hashtags)` |
| 交付【正文】写入 | feishu_ops.py `_update_delivery_fields` | regex 精确匹配替换已生效 |
| 口播 Prompt 全面优化 | prompt_builder.py | 角色 A/B、3-5句对话、4字标记、2-3张官方图、黄色高亮、音频转录透传 |
| src/ 热重载 | app.py | `importlib.reload()` 强制刷新 src/ 模块 |

### 已知问题 🔴

（当前无已知问题，全部已修复）

### 脚本类型：当前硬编码 `oral`

| 类型 | 风格 | 核心 |
|------|------|------|
| **混剪** (`mix`) | 图文式 | 插图和单人讲话是核心，每行 = 一段口播文案 + 一张配图素材 |
| **口播** (`oral`) | 对话式 | 双人角色对话（A/B 交替），只添加一些插图作为点缀素材 |

当前 `app.py` 中 `script_type` 硬编码为 `"oral"`（口播测试中），无 UI 选择器，无自动判断。后续需增加视频自判断代码（参考完整版 `ScriptGenerator.detect_type()`），根据视频内容自动识别类型并输出对应脚本。

### 飞书模板交付字段结构

Block [60] 是交付字段所在，type=2 (text)，两个 text_run element：

```
element 0: 【标题】：填写标题即可 \n【正文】：填写标题➕话题词即可 \n【是否发布】：未发布 \n
element 1: 【发布类型】：代发
```

`_update_delivery_fields` 阶段1 用 regex 精确匹配替换，无需回退。

### 配置系统

`config/requirements.json` → `config/load_requirements()` → `src/prompt_builder.py` → AI Prompt。

修改 `requirements.json` 后重启 Streamlit 即生效。

## 飞书 API 已知限制

- **不可用**：创建 Image Block（`block_type=27`）
- **表格**：`children[r*C + c] = cell(r,c)`，扁平 row-major
- **background_color**：只能 1-20，不能为 0
- **换行**：普通 text block 需拆 element；表格 cell 内可能不生效
- **非 text_run 元素**：更新 elements 时必须保留

## 部署到 ModelScope

```bash
git checkout -b deploy modelscope/master
git rm -rf .
git checkout main -- .
git commit -m "部署最新代码"
git push modelscope deploy:master
git checkout main && git branch -D deploy
```

## 边界问题（极简版未覆盖）

| 问题 | 完整版方案 | 影响 |
|------|-----------|------|
| 飞书文档永久残留 | `doc_registry.json` + 5 分钟 TTL 自动删除 | 每次生成留下一个文档，无自动清理 |
| 用户关闭浏览器 | atexit / session teardown hook | 临时视频文件 `data/<id>/downloads/` 永久留在磁盘；正在执行的步骤无法优雅中止 |

## 后续规划

1. **🟢 当前：口播端到端测试收尾** — 修复繁体字、A/B格式、交付标题话题词、断句方式等细节，验证通过后加入类型自动判断
2. **增加 AI 审核微调环节** — 生成脚本后、填入飞书前，增加一步 AI 自检：对照 Prompt 要求逐项校验输出，自动修正格式/内容偏差
3. **优化全流程时间开销** — 并行帧/音频提取、更快的模型
4. **用户subAgent** — 功能拓展（设定简单要求、输出文档数目和质量等）
5. **开拓管理 subAgent** — 一键修改配置并部署
