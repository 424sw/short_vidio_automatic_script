# Copilot 指令：短视频自动化脚本生成系统

> **先读 [CLAUDE.md](../../CLAUDE.md)** 了解项目结构、管道 5 步、两种脚本类型和约束体系。

---

## 核心心智模型

这是一个 **Prompt 工程 + 轻量管道** 的项目。质量靠 Prompt 驱动，Python 代码只是管道和兜底。

**黄金法则：先改 Prompt，再动代码。Prompt 能解决的不要写代码解决。**

## 最常见的错误模式（不要重蹈覆辙）

### 1. 头痛医脚 — 代码修 Prompt 的问题

如果 AI 输出质量差（全是单句、没有断句、广告生硬），先问：**Prompt 里有没有捆住 AI 手脚的约束？**

- ❌ Prompt 写"严禁任何标点符号" → AI 不敢用逗号 → 每行只写一个长句 → 你去代码里加段数修复指令 = **错**
- ✅ 删掉 Prompt 里的禁令，让 AI 正常写 → 代码里的 `_normalize_mix_punctuation` 兜底 = **对**

**原则：Prompt 告诉 AI"做什么"，代码兜底"清理什么"。不要让 Prompt 变成代码的敌人。**

### 2. 单向约束收敛 — 任何"只拆不合"或"只合不拆"的指令都会导致极端化

- `micro_adjust` 段数修复只写"3→2 合并"→ 所有行收敛到 2 句
- `micro_adjust` 段数修复只写"2→3 拆分"→ 所有行收敛到 3 句
- **必须双向化**："2 和 3 哪个多就把多数方少数化（可拆可合）"

**任何不对称的修复指令，都会让输出收敛到一个极端。**

### 3. 程序化变换时机错位 — normalize 必须在 review 之前

`_normalize_mix_punctuation` 会改变文本内容（删逗号/句号），从而改变 `\n` 切分出的段数。

- ❌ normalize 嵌在 `micro_adjust()` 内部 / review 之后 → review 看到的段数 ≠ 最终段数
- ✅ normalize 在 `review()` 第一次调用之前执行 → review 看到的是最终文本

**任何改变"review 检查指标"的程序化操作，必须在 review 之前跑。**

### 4. 加机制而不是修机制 — 用户拒绝复杂化

这个项目的管道已经很完整：`generate → review → micro_adjust`。不要在 `app.py` 里加新的步骤、新的分支、新的 return 路径。改现有的 Prompt 和三个核心函数（`_check_*`、`micro_adjust`、`micro_adjust_markers`）。

### 5. 不看实际输出就诊断

用户经常贴出 AI 实际生成的脚本文本。逐行读、和 Prompt 对照、追踪文本在管道里被每个函数改了什么，最后断定根因。不要跳步。

---

## 关键架构约束

### 两种脚本类型（只有两种）

| 类型 | `script_type` | 数据字段 | 输出 |
|------|-------------|---------|------|
| 混剪 | `"mix"` | `title`, `hashtags`, `rows: [[文案, 素材], ...]` | 10-16 行，每行分段 |
| 口播 | `"oral"` | `title`, `hashtags`, `original_text`, `dialogs: [[角色, 对话], ...]`, `images` | 对话末尾有【标记】 |

### Prompt 约束体系（改了必须前后一致）

- **`src/prompt_builder.py`**：所有 Prompt 模板。混剪 `build_mix_prompt()` / 口播 `build_oral_prompt()`
- **`src/script_generator.py`**：
  - `review()` → 检查 Prompt 里的约束是否被满足（程序化，不调 AI）
  - `_check_*()` → 各维度检查函数（段数、标点、标记分布、AI 味…）
  - `micro_adjust()` → 调 AI 修复问题的 Prompt
  - `_normalize_mix_punctuation()` → 程序化清理混剪标点

**改 Prompt 里的约束 → 同时检查 `_check_*` 是否也需要改。反之亦然。**

### 审核微调管道

```
generate()  → ① review()      → needs_rollback? 回退重生成（最多1次）
            → ② normalize()   → review() 看到准确的最终文本
            → ③ micro_adjust() → AI 修复 → re-review()
                          └→ 口播: micro_adjust_markers()
```

### 飞书限制

- **不支持 Image Block**（`block_type=27`），素材列只能写文字描述
- 文档 5 分钟后自动删除

---

## 修改检查清单

修改任何文件前问自己：

1. **这个问题能只改 Prompt 解决吗？**（先看 `prompt_builder.py`）
2. **这个修改会让某件事变成单向吗？**（只拆不查、只合不分、只增不减 → 收敛到极端）
3. **改 Prompt 约束后，`_check_*` 逻辑对齐了吗？**
4. **改 `_check_*` 条件后，`micro_adjust` 的修复指令覆盖了新的失败模式吗？**
5. **改程序化变换（如 normalize）后，它在 review 之前执行吗？**
6. **我是在修复现有机制，还是在加新机制？**（加新机制前默认不允许）

---

## 测试重启流程

```
find . -name "__pycache__" -type d -exec rm -rf {} +
netstat -ano | grep 8501 | awk '{print $NF}' | sort -u | while read pid; do taskkill //F //PID $pid; done
streamlit run app.py --server.port 8501
```

**不要写单元测试文件，用户不需要。改完代码直接重启让用户测试。**
