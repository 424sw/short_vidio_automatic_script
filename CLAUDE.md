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

## 当前状态（截至 2026-06-06）

### app.py
极简版 ~300 行，纯 4 步管道。无管理后台、多脚本、断点恢复、进度面板。

### 关键修复

| 修复内容 | 文件 | 方式 |
|---------|------|------|
| 标题含 #话题词 | feishu_ops.py `fill_mix_script` | `mix_title = title + " " + " ".join(f"#{t}" for t in hashtags)` |
| 交付【正文】写入 | feishu_ops.py `_update_delivery_fields` | 用 regex `re.sub` 对 `【标题】：`/`【正文】：` 后的内容进行 lambda 替换 |
| 表格多行换行 | feishu_ops.py `_fill_mix_table` / `_fill_oral_table` | `update_text_block(..., multiline=True)` 将 `\n` 拆为多个 text_run element |
| 软广植入 | prompt_builder.py | 🔴 硬性指令：前50%/第一个品牌/产品库匹配 |
| 话题词校验 | script_generator.py `_validate` | hashtags 不足 3 个抛 ScriptGeneratorError |
| rows 跨列解包 | feishu_ops.py `_fill_mix_table` | `for i, row in enumerate(rows_data)` + 索引取值，兼容 AI 输出超过 2 列 |

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

## 后续规划

1. **优化全流程时间开销** — 并行帧/音频提取、更快的模型
2. **设计全流程 UI** — 恢复进度面板、脚本类型选择
3. **开拓管理 subAgent** — 一键修改配置并部署
4. **口播脚本端到端测试** — `script_type` 当前硬编码 `"mix"`
5. **表格单元格换行** — `multiline=True` 拆分 element 方式是否生效待验证
