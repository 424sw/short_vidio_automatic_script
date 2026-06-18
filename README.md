---
sdk: streamlit
license: Apache License 2.0
deployspec:
  entry_file: app.py
---

# 短视频自动化脚本生成系统

输入抖音视频链接 → 下载视频 → 语音转文字 → AI 生成混剪/口播脚本 → AI 审核微调 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

核心目标：**仿写** — 保留原视频的信息密度和节奏，用全新措辞创作新脚本。

部署于 **ModelScope 创空间**。

## 本地启动

```bash
pip install -r requirements.txt
python tools/setup_models.py   # 一次性，~462MB
streamlit run app.py           # http://localhost:8501
```

## 两种脚本类型

| 类型 | 形式 | 输出 |
|------|------|------|
| **混剪** (`mix`) | 单人图文讲解 | 文案 + 配图素材 |
| **口播** (`oral`) | A/B 双人角色对话 | 对话 + 原片还原文案 |

用户手动选择。

## 管道 5 步

1. **提取视频** — 解析抖音链接（支持短链接跟踪、分享文本自动提取 URL），FFmpeg 下载去水印视频
2. **语音转文字** — faster-whisper-small 本地模型（标准/精细两档质量），CPU 推理，注入领域上下文减少音近错字
3. **生成脚本** — 基于音频转录仿写，一次 AI 调用生成 JSON。混剪 300-400 字，口播 400-500 字
4. **审核微调** — 程序化检查六维度（格式/长度/相似度/AI 味/段数/标记分布），AI 微调修正，程序兜底
5. **飞书文档** — 复制模板 → 设公开权限 → 填充内容 → 返回链接。5 分钟后自动删除

详细架构见 [CLAUDE.md](CLAUDE.md)。

## ModelScope 创空间部署

1. 在 [ModelScope 创空间](https://www.modelscope.cn/studios) 创建新空间
2. **创建时"接入 SDK"必须选 Streamlit**（不能选 Gradio）
3. Push 代码到创空间 Git 仓库
4. 空间页面点「重启空间展示」

### 部署命令

```bash
git checkout -b deploy modelscope/master
git rm -rf .
git checkout main -- .
git commit -m "部署最新代码"
git push modelscope deploy:master
git checkout main && git branch -D deploy
```

### 如果需要更换 API 密钥

在创空间设置 → 环境变量中添加：

| 变量名 | 说明 |
|--------|------|
| `AGNES_API_KEY` | AI API 密钥 |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |

不配置也可正常运行 — 代码内置了加密的默认值。

## 修改规则

直接编辑 `config/requirements.json`（行数范围、广告品牌、产品介绍库等），重启 Streamlit 生效。推代码到创空间仓库即可同步到线上。

## 依赖的外部服务

- **Agnes AI**：提供文本生成能力
- **飞书开放平台**：创建文档、填写内容、设置权限
- **FFmpeg**：视频下载和音频提取（自动检测，支持 imageio-ffmpeg 兜底）

## 项目结构

```
├── app.py                         ←  Streamlit 入口：5 步管道
├── src/
│   ├── __init__.py                ←  包导出
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
