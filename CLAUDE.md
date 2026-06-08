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
│   ├── video_analyzer.py          ←   ② 分析：提取音频 + 转录 + AI 综合（无抽帧）
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
| ① 分析参考视频 | 步骤 2 | 视频标题 + 音频转录文本 | 视频结构分析、完整口播文案、风格特征、关键信息点（synthesis） | `AGNES_MODEL` | ✅ 已实现 |
| ② 检测脚本类型 | 步骤 2 | synthesis + 视频标题 | `"mix"` 或 `"oral"` | `AGNES_MODEL` | ✅ 已实现 |
| ③ 生成脚本内容 | 步骤 3 | synthesis + Prompt 要求 | 混剪/口播脚本 JSON | `AGNES_MODEL` | ✅ 已实现 |
| ④ 审核微调 | 步骤 4 | 已生成脚本 + 原始 Prompt | 校验修正后的脚本 | `AGNES_MODEL` | ✅ 已实现 |

### ① 分析参考视频
[VideoAnalyzer.synthesize()](src/video_analyzer.py#L182)：将语音转文字文本发给 AI，输出视频结构、完整口播文案、风格特征、关键信息点。不再分析视频帧。

### ② 检测脚本类型
[ScriptGenerator.detect_type()](src/script_generator.py#L135)：在步骤 2 末尾调用，AI 直接根据音频转录文字判断是单人讲解还是多人对话。用户可在输入面板手动选择「自动检测 / 混剪 / 口播」，自动检测失败时默认回退 `"mix"`。

### ③ 生成脚本内容
[ScriptGenerator.generate()](src/script_generator.py#L22)：将 synthesis 注入混剪/口播 Prompt 模板，AI 输出严格 JSON（title / hashtags / rows 或 dialogs / images / original_text），经 `_validate()` 校验后返回。最多重试 3 次。

### ④ 审核微调
[ScriptGenerator.review()](src/script_generator.py#L127) + [build_review_prompt()](src/prompt_builder.py#L247)：在步骤 4 调用，将已生成的脚本 JSON 连同原始生成 Prompt 回传给 AI，逐项对照审核清单校验，自动修正格式偏差和内容缺失。审核采用更低温度（`temperature=0.2`）以保持稳定。失败不阻塞，回退使用原脚本。

## 当前状态（截至 2026-06-07）

### 2026-06-07：质量选项精简 + 精细模式 STT 增强 🎯

**目的**：删除不常用的"快速"质量，保留"标准"和"精细"；精细模式使用更高精度的语音转文字。

**改动范围**：

| 文件 | 改动 |
|------|------|
| [app.py](app.py) | 质量选项 `fast/standard/fine` → `standard/fine`；`step2_analyze()` 传入 `quality=`；精细模式时间估算上调至 90-180s |
| [video_analyzer.py](src/video_analyzer.py) | `_transcribe_audio()` 接收 `quality` 参数：`standard` → int8 + beam=5；`fine` → float32 + beam=10 |
| [prompt_builder.py](src/prompt_builder.py) | `get_quality_config()` 简化：删除 `fast` 档 + 帧相关字段，更新为 STT 精度描述 |

**效果**：
- 标准模式：int8 量化 + beam=5，约 60-120s（和不改前一样）
- 精细模式：float32 全精度 + beam=10，约 90-180s（更准但更慢）

### 2026-06-07：去除抽帧 + 帧分析 ⚡

**目的**：减少时间开销。抽帧 + 视觉 AI 分析是管道中耗时最长的环节（10-20 张图 base64 → vision API），实测约 30-60 秒。去除后分析步骤只剩音频提取 + Whisper 转录 + 纯文本 AI 综合。

**改动范围**：

| 文件 | 改动 |
|------|------|
| [video_analyzer.py](src/video_analyzer.py) | 删除 `extract_frames()`、`_analyze_frames()`、`_parse_frame_results()`；`synthesize()` 不再接收帧数据 |
| [prompt_builder.py](src/prompt_builder.py) | `build_synthesis_prompt()` 签名简化为 `(video_title, audio_transcript)`，Prompt 不再提及帧 |
| [app.py](app.py) | `step2_analyze()` 不读 `frame_analysis`；时间估算下调 |

**保留**：
- 质量选择 UI 控件，后续改为 `standard/fine` 两档（控制 Whisper STT 精度）

### 2026-06-07：重试逻辑优化 ⏱️

**目的**：消除无谓等待，把质量检查统一交给审核微调步骤。

**核心思路**：生成步骤不再因格式问题重头来，一次生成 + JSON 修复即交付，质量问题由审核步骤集中修。

**改动范围**：

| 文件 | 改动 |
|------|------|
| [script_generator.py](src/script_generator.py) | `generate()` 移除校验 → 重试循环（原来 3 次），改为一次 AI 调用 + 一次轻量 JSON 修复；`_validate()` 保留但仅用于审核步骤 |
| [script_generator.py](src/script_generator.py) | `review()` 重试次数从 2→3 次，失败用**最后一版**（不再回退原版），返回 `(script, note)` 元组 |
| [app.py](app.py) | `step4_review()` 适配 tuple 返回值，进度描述显示审核结果（如「审核通过」/「审核未完全通过，使用最后一版（请人工复核）」） |
| [feishu_ops.py](src/feishu_ops.py) | `_request()` 新增 95201 重试：等 1.5s → 3s → 7s，最多 3 次，解决飞书模板复制后元数据异步初始化问题 |

**效果**：
- `generate()` 省掉最多 2 次完整 AI 调用（~40s）
- `review()` 从 2 次→3 次加固，但实际很少触发第 3 次
- 飞书 95201 不再导致整个流程失败
- 进度面板显示审核结果，用户可判断是否需要人工复核

### 2026-06-07：脚本质量加固 🛡️

**目的**：解决口播脚本输出不稳定的两个核心问题 — 格式缺失（【标记】丢失）和内容质量（人机感重/与参考视频雷同）。

**问题根源**：
- 生成 Prompt 和审核清单都要求了【标记】，但 `_validate()` 不检查 → 漏检
- 生成 Prompt 和审核清单都没提「人机感」「相似度」→ AI 不知道要控制

**改动范围**：

| 文件 | 改动 |
|------|------|
| [script_generator.py](src/script_generator.py) | `_validate()` 新增口播脚本检查：①对话轮数（必须正好20轮）②角色名（只能是 "A"/"B"）③每轮末尾必须有【标记】；新增 `_compute_similarity()` 方法（字符三元组 Jaccard 相似度） |
| [script_generator.py](src/script_generator.py) | `review()` 新增参数 `synthesis`/`audio_transcript`/`target_chars`，调用前先计算相似度，>40% 时在审核 Prompt 中加入降重警告 |
| [prompt_builder.py](src/prompt_builder.py) | ①`build_oral_prompt()`：强化【标记】要求（「没有标记会被拒绝」）、新增「人机感要求」栏、明确「标题不含 #话题词」；②`build_mix_prompt()`：新增「人机感要求」栏和「措辞差异化」要求 |
| [prompt_builder.py](src/prompt_builder.py) | `build_review_prompt()`：审核清单新增第7/8项（内容长度 + 人机感检查），新增相似度超标警告段落；签名增加 `similarity`/`ref_word_count` |
| [app.py](app.py) | `step4_review()` 调用 `gen.review()` 时传入 `synthesis`/`audio_transcript`/`target_chars` |

**相似度计算原理**：
- 取脚本所有文本 + 参考视频文本（transcript + synthesis）
- 提取字符三元组（character trigrams），无需中文分词
- Jaccard = 交集 / 并集，> 0.4 视为相似度过高
- 相似度超标时，审核 Prompt 会警告 AI 降重改写

**三个环节对比（修改后）**：

| 检查项 | 生成 Prompt | 审核清单 | `_validate()` |
|--------|:--:|:--:|:--:|
| 对话轮数（8-20轮自适应） | ✅ | ✅ | ✅ **NEW**（范围检查） |
| 角色名（A/B） | ✅ | ✅ | ✅ **NEW** |
| 每轮末尾【标记】 | ✅✅ 强化 | ✅✅ 强化 | ✅ **NEW** |
| 人机感 | ✅ **NEW** | ✅ **NEW** | ❌ (AI 自查) |
| 内容长度匹配 | ✅ | ✅ **NEW** | ❌ (max_tokens 控制) |
| 相似度 < 40% | ✅ **NEW** | ✅ **NEW** | ✅ 代码计算 |

### 2026-06-07：执行层修复（仿写粒度 + 假取消 + 类型误判）

**目的**：上节的 Prompt 层加固后，测试发现三个执行层问题：①内容远超参考视频（AI 扩写而非仿写）②偶尔假取消（长步骤触发热备超时）③口播被误判为混剪。

**根因与修复**：

| 问题 | 根因 | 修复 |
|------|------|------|
| 内容远超参考视频 | `max_tokens` 预算太宽裕（200字给1000 token） | 公式收紧为 `(target_chars×1.5 + 300) × 1.3`，200字→780 token |
| 对话轮数强制20轮 | 短视频也要求20轮，迫使 AI 灌水扩写 | `requirements.json` 改为 `[8, 20]`，按 `target_chars/15` 动态建议轮数；`_validate()` 只检查范围 |
| 假取消 | 心跳只在 `st.rerun()` 时刷新，Whisper 90s阻塞导致超时 | 新增 `_touch_heartbeat()`，每个步骤开头主动刷新心跳 |
| 口播误判成混剪 | synthesis + 标题信息不足 | `detect_type()` 改为只看音频转录文字，不依赖 synthesis |
| AI 修不好标记 | 审核3次失败后交付缺标记的脚本 | `review()` 新增 `_fix_markers()` 兜底，程序化正则补标记；新增 `_find_missing_markers()` 列出缺失轮号 |

### 2026-06-07：排队机制已移除 🔧

**目的**：测试/部署需要允许多进程并行，取消排队机制。

**改动范围（全部在 [app.py](app.py)）**：

| 函数/位置 | 改动 | 说明 |
|-----------|------|------|
| `_acquire_lock()` | 注释原始逻辑 → 直接 `return True` | 锁禁用，允许并行 |
| `_join_queue()` | 注释原始逻辑 → `pass` | 不再加入等待队列 |
| `_leave_queue()` | 注释原始逻辑 → `pass` | 不再从队列移除 |
| `_is_my_turn()` | 注释原始逻辑 → 直接 `return True` | 跳过排队检查 |
| `_touch_lock()` | 注释原始逻辑 → `pass` | 不刷新锁心跳 |
| `_release_lock()` | 注释原始逻辑 → `pass` | 不释放锁 |
| `_handle_beacon()` | 注释原始逻辑 → `pass` | 不处理浏览器信标 |
| `_check_cancel()` | 注释断连检测部分 | 信标关闭后断连检测无效 |
| 按钮点击 `render_input_panel()` | 注释排队分支 → 直接进入管道 | 不检查锁，不加入队列 |
| `render_waiting_panel()` | 注释原始逻辑 → 直接回输入面板 | 防止意外进入等待 |
| `render_progress_panel()` | 注释心跳 + 信标 JS 注入 | 不发送 ping/close |
| `main()` | 注释 `_handle_beacon()` 调用 | 不处理信标请求 |

**恢复方式**：所有原始代码保留在 `# ═══════` 注释块内，删除顶部的 `return True` / `pass` + 取消注释原始代码即可完全恢复。

**影响**：
- ✅ 多用户可同时生成，不再排队等待
- ⚠️ 并发无控制，ModelScope 2 vCPU 16GB 下多用户同时跑可能 OOM
- ⚠️ 关闭 Tab 后后台进程继续跑完（原来靠信标即时释放锁）
- ⚠️ 文档过期仍靠被动触发，无变化

### 本次对话已完成 ✅

| 改动 | 文件 | 说明 |
|------|------|------|
| WebSocket keepalive | `.streamlit/config.toml` | `[server]` 配置，后台标签页不再弹重连 |
| 删除 beforeunload 弹窗 | `app.py` | 删 `_inject_exit_guard()`，步骤过渡不打扰 |
| 浏览器 beacon 机制 | `app.py` | JS 每 15s ping + 关闭发 close beacon → 即时释放锁 |
| 守护心跳线程移除 | `app.py` | 旧心跳线程导致僵尸锁永不过期，已删 |
| `_LOCK_STALE_SECONDS` 调整 | `app.py` | 30s → 360s（6 分钟兜底） |
| `_check_cancel()` 加断连检测 | `app.py` | 检查 `.closed` 和 `.browser_seen` 超时 |
| 文档过期改为文件队列 | `app.py` | `threading.Timer` → `data/.expiry_queue` JSONL，`main()` 每次检查 |
| 文档标题去 hex 后缀 | `config/__init__.py`, `feishu_ops.py` | 删 `session_suffix` |
| `target_chars` 计算修复 | `app.py` | 正则修复 + 无音频时用时长估算 |
| `max_tokens` 动态计算 | `script_generator.py` | 根据 `target_chars` 动态限制输出预算 |
| 飞书删除 API 加 type | `feishu_ops.py` | `delete_document()` 补 `params={"type": "docx"}` |
| 输出面板提示 | `app.py` | 「5分钟后且退出网页后，文档自动删除」 |

### 已知问题 🔴

1. **内容长度控制不精确** — 当前 `max_tokens` 动态约束 + Prompt 硬性长度约束 + 审核步骤复查，已大幅改善但仍非精确控制。AI 本质上是概率输出，无法像程序一样精确到字节。
2. **文档过期队列无锁保护** — `_enqueue_expiry()` 用 `open("a")` 追加写入，`_cleanup_expired_docs()` 用 `write_text("w")` 重写整个文件。`main()` 和 `render_result_panel()` 各调用一次 `_cleanup_expired_docs()`，同一 rerun 周期内如果 `write_text` 执行期间 `open("a")` 也在写入，文件内容会丢失，导致所有过期文档条目被清空。
3. **孤儿文档** — `create_and_fill()` 中 `copy_template()` 成功后如果 `fill_*()` 失败，模板副本已在飞书创建但 `created_ids` 未包含它，永远不会被追踪/过期清理。
4. **刷新页面后文档链接丢失** — `step=6` 结果面板的文档链接存储在 `st.session_state` 中，浏览器刷新后新 session 没有历史数据 → 用户看到空白输入面板 → 以为文档已被删。
5. **`_cleanup_stale_data()` 进程内只运行一次** — 标记文件 `data/.cleanup_done` 写入后永不删除。进程生命周期内只会清理一次残留 session 目录，后续异常退出的 session 残留文件不会自动回收。
6. **`_fix_markers()` 仅 10 个预设标记** — 超过 10 轮对话时标记循环重复，且不读 `requirements.json` 的情绪选项列表，完全硬编码。
7. **GitHub Actions Lint 持续报错** — 本地 `ruff check` 已全部通过（2026-06-07 修复），但 GitHub Actions 的 `astral-sh/ruff-action@v3` 仍报失败。可能原因：远程 ruff 版本/配置与本地不一致。暂不影响功能，后续排查。
8. ~~**多脚本输出质量不一致**~~ ✅ 已修复（2026-06-07）：`_validate()` 已新增口播脚本的【标记】、角色名、对话轮数硬检查；生成/审核 Prompt 已强化人机感要求；已新增相似度计算 + 超标降重机制。

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

## 边界处理（2026-06-06 全面实现）

### 部署环境

ModelScope 免费版：2 vCPU / 16 GB 内存 / **单实例，多用户串行共享**。

### 并发控制：文件锁 + FIFO 队列

两文件实现：`data/.running`（当前持有者 session_id）+ `data/.queue`（等待队列，一行一个 session_id）。

流程图：用户点击「开始生成」→ `_acquire_lock()` 尝试创建锁文件（原子操作 `O_CREAT|O_EXCL`）→ 成功则进入管道 / 失败则 `_join_queue()` 排队 → 等待面板每 2 秒 `_is_my_turn()` 检查队首是否是自己且锁是否空闲 → 轮到时获取锁并 `clear_run()` 启动管道。

**浏览器信标**：进度面板每次渲染注入 JS 每 15 秒 fetch 存活 ping；`pagehide`/`beforeunload` 事件发送 close beacon → `_handle_beacon()` 释放锁。360 秒锁超时作为兜底（浏览器崩溃等极端情况）。

### 已知限制：运行中关闭 Tab

**Whisper 转录是 Python 单进程内的阻塞调用，Streamlit 无法中途终止。** 关闭浏览器后后台进程会继续跑完整个管道（包括创建飞书文档）。最长等待 ≈ Whisper 超时（5 分钟）。这是 Python/Streamlit 单进程模型的根本限制，要彻底解决需将 Whisper 改为独立子进程。

### 临时文件全生命周期

- 所有临时文件统一在 `data/<session_id>/` 下
- `_cleanup_session()` = `shutil.rmtree(data/<sid>/)` + `_release_lock()`
- 每个步骤异常/取消均调用 `_cleanup_session()`
- 应用启动时 `_cleanup_stale_data()` 清空所有残留（通过 `data/.cleanup_done` 标记确保进程生命周期内只执行一次）

### 用户取消

**取消**：进度面板「⏹ 取消生成」按钮 → `cancel_requested = True` → `_check_cancel()` 抛出 `StepCancelledError` → 清理 + 回到输入面板。

### 5 分钟文档自动删除

`step5_feishu()` 调用 `_enqueue_expiry()` 将文档 ID + 过期时间写入 `data/.expiry_queue`（JSONL）。`_cleanup_expired_docs()` 在 `main()` 入口和结果面板入口检查队列，到期调用飞书删除 API。**已知限制**：需有人访问网站才触发清理扫描，页面不会自动轮询。

### 部分失败不丢结果

`step5_feishu()` 循环创建文档：单个失败跳过，成功的仍然返回。汇总：`3/5 个文档创建成功`。

### 动态磁盘管理

- FFmpeg 下载限时长 `-t 300`（`MAX_VIDEO_DURATION_SEC = 300`）
- 下载前检查剩余空间（`MIN_FREE_DISK_BYTES = 100MB`）
- requests 流式下载时监控大小，超过剩余空间 50% 中止
- 不写死文件大小上限，运行时动态判断

### API 节流 & 重试

- 批量生成脚本：每个间隔 1.5s（`generate_multiple()`）
- AI API：429/503 指数退避重试（`_call_api()`）
- 输出数目上限：`MAX_SCRIPT_COUNT = 5`，UI + 服务端双重 `min(count, MAX_SCRIPT_COUNT)` 约束

### 重试逻辑全景（2026-06-07 已优化）

| 文件 | 位置 | 场景 | 次数 | 备注 |
|------|------|------|------|------|
| `script_generator.py` | `generate()` | 不再校验重试 | 1 | **已优化**：一次生成 + 一次 JSON 修复，质量交给 review |
| `script_generator.py` | `_call_api()` | API 429/503 限流 | 3 | 指数退避，必要开销 |
| `script_generator.py` | `review()` | 审核校验失败 | 3 | **已加固**：失败用最后一版（不再回退原版） |
| `feishu_ops.py` | `_request()` | 飞书 API 限流/Token过期/95201 | 3 | **新增 95201 重试**：等 1.5s→3s→7s |
| `douyin_extractor.py` | 视频提取 | 网络请求失败 | 3 | 指数退避 |

### 错误提示

输入面板仅当错误关键词包含「链接」或「Douyin」时才显示「请检查视频链接是否有效」提示，其他错误（抽帧失败、API 超时等）只显示错误信息本身。

### 配置常量

```python
MAX_SCRIPT_COUNT = 5
DOC_TTL_SECONDS = 300        # 飞书文档 5 分钟过期
MAX_VIDEO_DURATION_SEC = 300 # FFmpeg 下载最长 5 分钟
MIN_FREE_DISK_BYTES = 100 * 1024 * 1024  # 最低 100MB 磁盘
WHISPER_TIMEOUT_SEC = 300    # Whisper 转录 5 分钟超时
```

## 后续规划

1. **飞书图片插入** — API 不支持 `block_type=27`，需另辟蹊径。
2. **管理界面/SubAgent** — 在原有的用户界面基础上增加管理界面
3. **其他优化** — 输出面板多脚本合并到一个文件夹；输入面板可输入个性要求（在精细输出质量中实现）；输出内容相似度高；生成过程偶尔报错（比如脚本生成错误）；“自动检测”功能；手机端UI优化；“精细”功能实测不可用其他待发现问题
