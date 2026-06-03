---
sdk: streamlit
license: Apache License 2.0
deployspec:
  entry_file: app.py
---

# 短视频自动化脚本生成系统

输入抖音视频链接 → AI 全模态分析 → 生成混剪/口播脚本 → 自动填入飞书模板 → 返回可分享的飞书文档链接。

## 本地启动

```bash
pip install -r requirements.txt

# 用户界面（端口 8501）
streamlit run app.py

# 管理后台（端口 8502）
streamlit run admin.py --server.port 8502 --server.headless true
```

两个可以同时运行，互不影响。

## 界面说明

| 界面 | 地址 | 用途 |
|------|------|------|
| 用户界面 | `http://localhost:8501` | 粘贴抖音链接 → 生成脚本 → 获得飞书文档 |
| 管理后台 | `http://localhost:8502` | 用大白话修改脚本规则，无需懂编程 |

## 管理后台用法

打开管理后台后，在对话框用自然语言描述你想改的规则：

- "把混剪行数改成 8-12 行"
- "广告品牌改成小红书"  
- "话题词改成 2-4 个"
- "标题字数改成 10-20 字"

AI 会自动翻译成配置变更，你确认后立刻生效。

## ModelScope 创空间部署

1. 在 [ModelScope 创空间](https://www.modelscope.cn/studios) 创建新空间
2. **创建时"接入 SDK"必须选 Streamlit**（不能选 Gradio）
3. Push 代码到创空间 Git 仓库
4. 空间页面点「重启空间展示」

### 如果需要更换 API 密钥

在创空间设置 → 环境变量中添加：

| 变量名 | 说明 |
|--------|------|
| `AGNES_API_KEY` | AI API 密钥 |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |

不配置也可正常运行——代码内置了加密的默认值。

## 依赖的外部服务

- **Agnes AI**：提供文本生成和图像识别能力
- **飞书开放平台**：创建文档、填写内容、设置权限
- **FFmpeg**：视频抽帧和音频提取（自动检测，支持 imageio-ffmpeg 兜底）

## 修改规则后如何同步到线上

本地管理后台修改后 → `git push` 到创空间仓库 → 创空间自动重新部署 → 线上生效。

## 项目结构

```
├── app.py              # 用户界面（Streamlit）
├── admin.py            # 管理后台（自然语言改配置）
├── config.py           # 配置、密钥、Prompt
├── src/
│   ├── douyin_extractor.py   # 抖音链接解析
│   ├── video_analyzer.py     # 视频分析
│   ├── script_generator.py   # 脚本生成
│   ├── feishu_ops.py         # 飞书 API
│   └── session_manager.py    # 会话持久化
├── config/requirements.json  # 脚本规则配置
└── requirements.txt          # Python 依赖
```
