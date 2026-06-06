# 短视频自动化脚本生成系统

## 项目概述

输入抖音视频链接 → AI 全模态分析视频 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

部署于 **ModelScope 创空间**。

## 快速开始

```bash
pip install -r requirements.txt
python tools/setup_models.py   # 一次性，~462MB
streamlit run app.py           # http://localhost:8501
```

## 项目结构

```
├── app.py                         ← 🚪 入口：5 步管道
├── src/                           ← ⚙️ 核心引擎
│   ├── douyin_extractor.py        ←   ① 提取：URL → 下载
│   ├── video_analyzer.py          ←   ② 分析：抽帧 + 转录 + AI 综合 + 类型检测
│   ├── prompt_builder.py          ←   📝 Prompt：综合分析/混剪/口播/审核
│   ├── script_generator.py        ←   ③ 生成 + ④ 审核：AI → JSON → 校验 → 自检
│   └── feishu_ops.py              ←   ⑤ 飞书：模板复制 → 填充 → 公开
├── config/                        ← ⚙️ 密钥 + requirements.json + admin.json
├── tools/                         ← 🛠️ setup_models.py + faster-whisper 模型
└── _archive/                      ← 🗑️ 旧版完整代码备份
```

## 管道 5 步

| 步骤 | 函数 | 核心文件 |
|------|------|---------|
| 1. 提取视频 | `step1_extract()` | [douyin_extractor.py](src/douyin_extractor.py) |
| 2. AI 分析 + 类型检测 | `step2_analyze()` | [video_analyzer.py](src/video_analyzer.py) |
| 3. 生成脚本 | `step3_generate()` | [script_generator.py](src/script_generator.py) |
| 4. 审核微调 | `step4_review()` | [script_generator.py](src/script_generator.py) + [prompt_builder.py](src/prompt_builder.py) |
| 5. 飞书文档 | `step5_feishu()` | [feishu_ops.py](src/feishu_ops.py) |

## 四次 AI 调用

| 调用 | 所在步骤 | 输入 | 输出 | 模型 | 状态 |
|------|---------|------|------|------|------|
| ① 分析参考视频 | 步骤 2 | 抽帧图片 + 音频转录 | 视频结构分析、完整口播文案、风格特征、关键信息点（synthesis） | `AGNES_MODEL` | ✅ 已实现 |
| ② 检测脚本类型 | 步骤 2 | synthesis + 视频标题 | `"mix"` 或 `"oral"` | `AGNES_MODEL` | ✅ 已实现 |
| ③ 生成脚本内容 | 步骤 3 | synthesis + Prompt 要求 | 混剪/口播脚本 JSON | `AGNES_MODEL` | ✅ 已实现 |
| ④ 审核微调 | 步骤 4 | 已生成脚本 + 原始 Prompt | 校验修正后的脚本 | `AGNES_MODEL` | ✅ 已实现 |

### ① 分析参考视频
[VideoAnalyzer.synthesize()](src/video_analyzer.py#L249)：将抽帧描述 + 语音转文字合并，由 AI 输出视频结构、完整口播文案、风格特征、关键信息点。

### ② 检测脚本类型
[ScriptGenerator.detect_type()](src/script_generator.py#L91)：在步骤 2 末尾调用，AI 根据 synthesis 判断视频适合混剪还是口播。用户可在输入面板手动选择「自动检测 / 混剪 / 口播」，自动检测失败时默认回退 `"mix"`。

### ③ 生成脚本内容
[ScriptGenerator.generate()](src/script_generator.py#L22)：将 synthesis 注入混剪/口播 Prompt 模板，AI 输出严格 JSON（title / hashtags / rows 或 dialogs / images / original_text），经 `_validate()` 校验后返回。最多重试 3 次。

### ④ 审核微调
[ScriptGenerator.review()](src/script_generator.py#L127) + [build_review_prompt()](src/prompt_builder.py#L247)：在步骤 4 调用，将已生成的脚本 JSON 连同原始生成 Prompt 回传给 AI，逐项对照审核清单校验，自动修正格式偏差和内容缺失。审核采用更低温度（`temperature=0.2`）以保持稳定。失败不阻塞，回退使用原脚本。

## 当前状态（截至 2026-06-06）

### 混剪回测完成 ✅

口播 → 混剪回测通过。混剪输出已验证：
- 标题含话题词 ✅
- 品牌无粗体 ✅
- 话题词 4-5 个 ✅
- 交付【标题】不含话题词、封面标题不含话题词、【正文】含话题词 ✅
- 内容长度与参考视频相当 ✅

### 规划 3+4 已实现：类型自动判断 + 审核微调 ✅

1. **类型自动判断**：`ScriptGenerator.detect_type()` + UI selectbox「自动检测/混剪/口播」，默认自动检测。
2. **AI 审核微调**：`ScriptGenerator.review()` + `build_review_prompt()`，逐项对照审核清单校验修正，低温稳定，失败不阻塞。
3. **5 步管道**：①提取→②分析+检测→③生成→④审核→⑤飞书→结果，进度条 `/5`。

### 规划 6 已实现：输出质量 + 输出数目 + 内容长度约束 ✅

1. **三档质量**：`get_quality_config()` — 快速(5帧/简要)、标准(10帧/详细)、精细(20帧/最大详细)，影响抽帧数、vision_detail、synthesis max_tokens。进度面板时间估算随质量动态显示。
2. **多脚本输出**：`generate_multiple()` 批量生成，`_build_diversity_instruction()` 按 variation_seed 注入不同叙事角度和素材偏好（混剪 5 种+口播 5 种），不做重叠率校验。
3. **内容长度硬性约束**：`step3_generate()` 从音频转录计算原视频口播字数 → `target_chars`，注入 Prompt：脚本总字数必须在原视频的 80%-120% 范围内。
4. **UI 同行布局**：类型/质量/数目三控件 `st.columns` 并排，`clear_run()` bug 已修复（保存/恢复用户选择）。

### 输入面板 UI

```
抖音视频链接 [text_input                    ]
[脚本类型 ▼] [输出质量 ▼] [输出数目 ±]
[        开始生成脚本        ]
```

### 语音转文字 — 双重优化 ✅

1. **模型升级**：faster-whisper-tiny（75MB）→ small（462MB），中文识别大幅改善
2. **领域上下文注入**：`_build_whisper_initial_prompt()` 维护互联网热词库（主包、家人们、绝绝子、yyds…），作为 `initial_prompt` 传入 Whisper 解码器
3. **AI 纠错**：synthesis + oral prompt 均包含 ASR 典型错误示例，指导 AI 二次修复

### app.py — 2026-06-06 UI 重构

三面板独立化：`main()` 改为严格 `if/elif/else` 三路分支，面板互不干扰。进度面板重写：去除 `st.status()`（可折叠下拉框），改用 `st.progress()` 进度条 + 步骤标题 + `st.spinner()`。

### 已验证修复 ✅

| 修复内容 | 文件 | 方式 |
|---------|------|------|
| 三面板独立化 | app.py | `main()` 严格 if/elif/else |
| 进度面板重写 | app.py | 去 `st.status`，换进度条 + spinner |
| 表格多行换行 | feishu_ops.py | `multiline=True` 拆分 text_run 已生效 ✅ |
| 软广植入 | prompt_builder.py | 🔴 硬性指令：前50%/第一个品牌/产品库匹配 |
| 话题词校验 | script_generator.py `_validate` | hashtags 不足 4 个抛 ScriptGeneratorError |
| rows 跨列解包 | feishu_ops.py `_fill_mix_table` | `for i, row in enumerate(rows_data)` + 索引取值 |
| 口播 Prompt 全面优化 | prompt_builder.py | 角色 A/B、3-5句对话、4字标记、2-3张官方图、黄色高亮、音频转录透传 |
| Whisper tiny→small 升级 | video_analyzer.py, setup_models.py | 模型 ~75MB→~462MB，中文识别准确率大幅提升 |
| Whisper 领域上下文注入 | video_analyzer.py | `_build_whisper_initial_prompt()` — 互联网热词库 + 品牌/术语作为 `initial_prompt` 传入 Whisper，减少音近错字 |
| AI 转录纠错双重提示 | prompt_builder.py | synthesis + oral prompt 均加入 ASR 错误示例，指导 AI 逐句修复 |
| 混剪标题含话题词 | prompt_builder.py | 示例 + 要求均含 `#话题词` |
| 混剪品牌不加粗 | prompt_builder.py | 删除广告指令中的粗体要求 |
| 交付【标题】不含话题词 | feishu_ops.py `_update_delivery_fields` | `clean_title = re.sub(r'\s*#[^\s#]+', '', title)` |
| 封面标题不含话题词 | feishu_ops.py `fill_mix_script` / `fill_oral_script` | 传给 `_update_cover_title_bullet` 的标题清洗掉 `#xxx` |
| 话题词数量 4-5 个 | prompt_builder.py + script_generator.py | Prompt 🔴 硬性要求 + 示例 4 个 + 校验 ≥4 |
| 内容长度匹配参考视频 | prompt_builder.py | Prompt 新增约束：仿写篇幅与原视频文案一致 |
| src/ 热重载 | app.py | `importlib.reload()` 强制刷新 src/ 模块 |
| PostToolUse Hook 已移除 | settings.json | 无用且不可靠，已删除 |
| 类型自动检测 | script_generator.py `detect_type()` | AI 判断混剪/口播，异常回退 `"mix"` |
| UI 类型选择器 | app.py `render_input_panel()` | selectbox 三选一：自动检测/混剪/口播 |
| AI 审核微调 | script_generator.py `review()` + prompt_builder.py `build_review_prompt()` | 对照审核清单逐项校验，低温稳定，失败不阻塞 |
| 5 步管道 | app.py | 步骤重新编号：①提取→②分析+检测→③生成→④审核→⑤飞书 |
| 三档输出质量 | prompt_builder.py `get_quality_config()` | fast(5帧)/standard(10帧)/fine(20帧) + vision_detail + synthesis tokens |
| 多脚本批量生成 | script_generator.py `generate_multiple()` + `_build_diversity_instruction()` | variation_seed 驱动不同叙事角度和素材偏好 |
| 内容长度硬性约束 | prompt_builder.py + app.py `step3_generate()` | 从音频转录估算原视频字数 → target_chars → Prompt 80%-120% 约束 |
| 质量联动时间估算 | app.py `_get_step_estimates()` | 进度面板预计时间随质量档位动态显示 |
| clear_run() 吞掉用户选择 | app.py `render_input_panel()` | 点击按钮前保存 类型/质量/数目 → clear_run() → 恢复 |
| UI 同行布局 | app.py | 类型/质量/数目三控件 st.columns 并排 |

### ⚠️ Streamlit 缓存教训

**这是本项目最大的隐性坑。** Streamlit 只会热重载入口文件 `app.py`，已导入的 `src/` 模块**不会**自动刷新。`app.py` 已配置 `importlib.reload()` 解决，但 `.pyc` 缓存有时仍会残留。

**症状**：
- 改的代码逻辑没生效，报错信息跟实际代码不符
- `__pycache__/*.pyc` 是脏的，Python 加载旧字节码
- 多进程并存（`taskkill` 可能漏杀），浏览器随机连到旧进程

**正确做法（三步**）：
1. 清掉所有 `__pycache__/` 和 `*.pyc`（`find . -name "*.pyc" -delete`）
2. 杀掉端口上**所有** Streamlit 进程（`netstat -ano | grep 8501 | awk '{print $5}' | sort -u` 逐个 `taskkill //F //PID`）
3. 等端口释放后重新启动

### 已知问题 🔴

（当前无已知问题，全部已修复）

### 脚本类型：支持自动检测 + 手动选择

| 类型 | 风格 | 核心 |
|------|------|------|
| **混剪** (`mix`) | 图文式 | 插图和单人讲话是核心，每行 = 一段口播文案 + 一张配图素材 |
| **口播** (`oral`) | 对话式 | 双人角色对话（A/B 交替），只添加一些插图作为点缀素材 |

输入面板提供 `st.selectbox`：「自动检测 / 混剪 / 口播」，默认自动检测。自动检测由 `ScriptGenerator.detect_type()` 执行，AI 根据视频内容判断，失败默认回退 `"mix"`。手动选择时跳过 AI 检测，直接使用用户指定类型。

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

1. **🟢 混剪回测通过**
2. **🟢 语音转文字优化完成** — 模型升级 + 领域热词注入 + AI 二次纠错。
3. **🟢 类型自动判断已完成** — `detect_type()` + UI selectbox。
4. **🟢 AI 审核微调已完成** — `review()` + `build_review_prompt()`，5 步管道。
5. **🟢 输出质量/数目/长度约束已完成** — 三档质量 + 多脚本批量 + 字数硬性约束。
6. 优化全流程时间开销 — 抽帧/转录并行、更快的模型
7. 边界问题 — 飞书文档自动清理、临时文件生命周期等等
8. 飞书图片插入功能 — 飞书 API 不支持 block_type=27，需另辟蹊径
9. 开拓管理 subAgent — 一键修改配置并部署
10. 进一步优化 — 多脚本合并到一个文档而非拆成多个，用户可一键转存所有文档到自己的飞书文档库
