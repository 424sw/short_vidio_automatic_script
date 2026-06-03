"""
管理员控制台 — 用自然语言修改 requirements.json 脚本配置。

运行方式：
    streamlit run admin.py --server.port 8502 --server.headless true

不需要懂 JSON 或编程，用大白话描述即可。
"""
import json
import re
from pathlib import Path

import streamlit as st
from openai import OpenAI

from config import AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL

REQUIREMENTS_PATH = Path(__file__).parent / "config" / "requirements.json"

# ============================================================
st.set_page_config(
    page_title="配置管理",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    #MainMenu, footer, header { visibility: hidden; }
    .stDeployButton { display: none; }
    .stMarkdown h1 a, .stMarkdown h2 a, .stMarkdown h3 a { display: none; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# 文件读写
# ============================================================

def load_config() -> dict:
    with open(REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data: dict):
    with open(REQUIREMENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def config_to_text(data: dict) -> str:
    """把 JSON 转成可读的文本列表"""
    lines = []
    for section, items in data.items():
        if section.startswith("_"):
            continue
        lines.append(f"【{section}】")
        if isinstance(items, dict):
            for k, v in items.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {items}")
    return "\n".join(lines)


# ============================================================
# Session
# ============================================================

if "msgs" not in st.session_state:
    st.session_state.msgs = []

# ============================================================
# AI 调用
# ============================================================

SYSTEM_PROMPT = """你是一个短视频脚本生成系统的配置助手。用户用自然语言描述想要修改的规则，你负责将他的意图精确翻译为 JSON 配置的变更。

当前系统有三个配置节：
- **通用**：语言、返回格式、交付要求元规则
- **混剪**：标题字数、行数范围、文案/素材风格、广告植入
- **口播**：标题字数、对话轮数、角色格式、情绪选项、图片素材、对话结构
- **交付要求**：话题词数量/格式、【标题】【正文】等发布信息

你的回复必须只包含一个 JSON，格式：
```json
{
  "reply": "用通俗中文说明你做了什么修改",
  "config": { ... 修改后的完整配置 JSON ... }
}
```

规则：
1. 只修改用户明确提到的字段，其他保持原样
2. 保留 _说明 字段不要改
3. 数字就是数字，字符串就是字符串，类型要对
4. 如果用户请求不明确，reply 里说明需要澄清"""


def ask_ai(user_msg: str, current_config: dict) -> dict:
    client = OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY)

    config_json = json.dumps(current_config, ensure_ascii=False, indent=2)
    config_readable = config_to_text(current_config)

    resp = client.chat.completions.create(
        model=AGNES_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"当前配置的文字版：\n{config_readable}\n\n当前配置的 JSON：\n{config_json}"},
            {"role": "assistant", "content": "已读取完毕。请告诉我要怎么改。"},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=2000,
        temperature=0.2,
    )

    raw = resp.choices[0].message.content.strip()
    md = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    json_str = md.group(1) if md else raw
    start = json_str.find("{")
    end = json_str.rfind("}")
    if start >= 0 and end > start:
        json_str = json_str[start:end + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {"reply": f"AI 返回无法解析，请重试。原始回复：\n\n{raw[:500]}", "config": None}


# ============================================================
# UI
# ============================================================

st.title("脚本配置管理")
st.caption("用大白话改规则 → AI 翻译成配置 → 确认生效")

# 左侧：当前配置
with st.sidebar:
    st.markdown("### 当前生效的配置")
    current = load_config()
    st.code(config_to_text(current), language=None)

    st.divider()
    st.caption("💡 修改不会自动同步到 ModelScope 创空间。若需更新线上版本，请 push 代码到创空间仓库。")

# 右侧：对话
for i, msg in enumerate(st.session_state.msgs):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # 待确认的修改
        if msg.get("pending_config") and not msg.get("resolved"):
            cfg = msg["pending_config"]
            with st.expander("查看新旧对比", expanded=True):
                c1, c2 = st.columns(2)
                with c1:
                    st.caption("当前")
                    st.code(config_to_text(load_config()), language=None)
                with c2:
                    st.caption("修改后")
                    st.code(config_to_text(cfg), language=None)

            a1, a2 = st.columns([1, 1])
            with a1:
                if st.button("✅ 确认保存", key=f"save_{i}", type="primary"):
                    save_config(cfg)
                    msg["resolved"] = True
                    msg["content"] += "\n\n✅ 已保存。本地重启 Streamlit 后生效。"
                    st.rerun()
            with a2:
                if st.button("撤销", key=f"undo_{i}"):
                    msg["resolved"] = True
                    msg["content"] += "\n\n撤销。"
                    st.rerun()

if prompt := st.chat_input("例如：混剪行数改成 8-12 行、广告品牌改成小红书、话题词改成 2-4 个..."):
    st.session_state.msgs.append({"role": "user", "content": prompt})

    with st.spinner("..."):
        result = ask_ai(prompt, load_config())

    reply = result.get("reply", "")
    new_cfg = result.get("config")

    if new_cfg:
        st.session_state.msgs.append({
            "role": "assistant",
            "content": f"💬 {reply}",
            "pending_config": new_cfg,
            "resolved": False,
        })
    else:
        st.session_state.msgs.append({
            "role": "assistant",
            "content": f"💬 {reply}",
        })

    st.rerun()
