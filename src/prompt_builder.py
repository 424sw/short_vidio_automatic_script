"""Prompt 构建：视频分析 + 混剪/口播脚本生成。"""
from config import load_requirements


def get_product_descriptions() -> list:
    """获取产品介绍库（用于 Prompt 中）."""
    return load_requirements().get("产品介绍库", [])


def _build_product_section() -> str:
    """构建产品介绍库 Prompt 段落."""
    products = get_product_descriptions()
    if not products:
        return ""
    lines = [
        "\n## 📦 产品介绍库（必须从中选择一段）",
        "根据视频主题，选择最匹配的产品文案，紧跟品牌名之后（约20-40字）：",
    ]
    for i, p in enumerate(products):
        lines.append(f"{i+1}. 【{p.get('主题','')}】→ 适用：{p.get('适用场景','')} → 文案：{p.get('文案','')}")
    return "\n".join(lines)


def get_quality_config(_quality: str = "standard") -> dict:
    """质量配置（当前统一为「标准」）。保留参数以备后续扩展."""
    return {
        "label": "标准", "max_frames": 10,
        "est_time": "约 1 分钟",
        "vision_detail": "详细描述画面内容、文字、构图",
    }


def build_synthesis_prompt(frame_count: int, video_title: str,
                           descriptions: str, audio_transcript: str = "") -> str:
    """构建视频综合分析的 Prompt."""
    if audio_transcript:
        audio_hint = "，以及音频转录文字"
        audio_inst = (
            "根据音频转录文字和画面内容，还原视频完整口播文案，标注时间节点。\n\n"
            f"音频转录：\n{audio_transcript}"
        )
    else:
        audio_hint = ""
        audio_inst = "根据画面文字和场景推测口播文案，标注时间节点。"

    return f"""你是短视频内容分析师。请分析视频"{video_title}"的 {frame_count} 张关键帧{audio_hint}。

## 一、视频结构
开头钩子 → 中间展开 → 结尾总结（标注时间线）

## 二、完整口播文案
{audio_inst}

## 三、风格特点
视觉风格、节奏、情绪基调

## 四、关键信息点
5-8 个核心信息点

逐帧描述：\n{descriptions}"""


def build_mix_prompt(synthesis: str, custom_requirements: str = "") -> str:
    """构建混剪脚本生成的 Prompt。

    强制执行规则：
    - 3-5 个话题词
    - 软广在前50%行首次出现，是第一个被谈及的品牌
    - 软广后紧跟产品介绍库中匹配的文案
    - 无标点符号，靠换行分隔停顿
    """
    req = load_requirements()
    m = req["混剪"]
    lo, hi = m["行数范围"]
    ad = m.get("广告", {})
    brand = ad.get("品牌", "鱼泡直聘")
    _ad_position = ad.get("位置", "约前50%位置处")  # 保留读取，用于对齐配置
    hashtag_req = req.get("交付要求", {})
    hashtag_count = hashtag_req.get("话题词数量", "3-5个")
    products = _build_product_section()
    mid_point = hi // 2

    # ==== 广告植入指令（严格） ====
    ad_lines = [
        "## 🔴 广告植入（硬性要求，必须全部满足）",
        f"1. 品牌：**{brand}**（软广植入）",
        f"2. 必须在脚本的**前50%行**（即第1行到第{mid_point}行之间）首次提及「{brand}」",
        f"3. 「{brand}」必须是整个脚本中**第一个**被谈及的商业品牌/产品（不能先提其他品牌再提{brand}）",
        f"4. 提及「{brand}」后，必须紧接着从下方📦产品介绍库中，根据视频主题选择**最匹配**的一段文案（约20-40字），格式为：「{brand}，[选中的产品介绍文案]」",
        "5. 广告行中 {brand} 用**粗体**标记",
    ]
    ad_block = "\n".join(ad_lines)

    override = ""
    if custom_requirements and custom_requirements.strip():
        override = f"\n## 用户自定义要求（最高优先级）\n{custom_requirements.strip()}\n"

    return f"""你是短视频脚本策划。生成混剪脚本（**单人图文讲解**风格）。

视频画面 + 单人旁白口播 + 趣味素材穿插，每条口播文案配一张对应的素材图片。

{override}
## 视频分析
{synthesis}

## 输出格式（纯 JSON，不用 markdown 代码块包裹）

格式说明：rows 中每条文案是一段完整的口播内容（不是一句话），用 \\\\n 在适当位置换行来分隔意群、代替标点，表示语气停顿。

正确示例（注意文案内容中的 \\\\n 换行）：
{{{{
  "title": "零基础转行面试三大秘诀",
  "hashtags": ["转行求职", "面试技巧", "零基础"],
  "rows": [
    ["你是不是也像我一样\\n投了几十份简历\\n一个面试都没有", "功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包"],
    ["朋友推荐我上鱼泡直聘\\n上面岗位都是真实直招\\n而且还有新人培训\\n零基础也能快速上手", "小狗点头.jpg 小狗戴着眼镜在键盘前点头的表情包"]
  ]
}}}}

## 脚本要求
- 共 {lo}-{hi} 行，每行 = 一段完整口播文案（多句话，用换行分隔）+ 一张配图素材
- 风格：**单人讲解**，口语化，仿佛对着镜头跟观众聊天
- 每行文案必须是**一段完整的口语内容**（2-5句话），用自然换行（\\\\n）分隔来表示语言停顿，严禁任何标点符号
- 素材格式：{m.get('素材格式', '文件名.jpg 中文描述')}
- 素材风格：{m.get('素材风格', '尽量使用动物、表情包等趣味素材')}，每条素材与对应文案内容匹配
- 话题词：{hashtag_count}，中文短语，如：职场干货 #面试技巧 #求职

## 标题要求
- 标题字数：{m.get('标题字数', '15-25字')}
- **标题本身不要包含#话题词**（话题词单独放在 hashtags 数组中）
- 交付时系统会自动拼接标题+话题词

{ad_block}
{products}"""


def build_oral_prompt(synthesis: str, custom_requirements: str = "") -> str:
    """构建口播脚本生成的 Prompt。

    强制执行规则：
    - 正好 20 轮对话
    - 正好 20 条图片素材
    - 每轮对话【情绪标记】
    - 软广在前50%对话轮首次出现
    """
    req = load_requirements()
    o = req["口播"]
    dialog_count = o.get("对话轮数", 20)
    image_count = o.get("图片素材数量", 20)
    emotions = "、".join(o.get("情绪选项", []))
    products = _build_product_section()
    mid_dialog = dialog_count // 2

    # ==== 广告植入指令（严格） ====
    ad_lines = [
        "## 🔴 广告植入（硬性要求，必须全部满足）",
        "1. 品牌：**鱼泡直聘**（软广植入）",
        f"2. 必须在对话的**前50%轮次**（即第1轮到第{mid_dialog}轮之间），由某个角色首次提及「鱼泡直聘」",
        "3. 「鱼泡直聘」必须是整个脚本中**第一个**被谈及的商业品牌/产品",
        "4. 提及「鱼泡直聘」后，该角色必须紧接着从下方📦产品介绍库中选择**最匹配**的一段文案（约20-40字），自然地融入对话",
        "5. 广告内容的情绪标记应为【推荐】",
    ]
    ad_block = "\n".join(ad_lines)

    override = ""
    if custom_requirements and custom_requirements.strip():
        override = f"\n## 用户自定义要求（最高优先级）\n{custom_requirements.strip()}\n"

    return f"""你是短视频脚本策划。生成口播脚本（**A/B角色对话**风格）。

两个角色通过对话形式讨论话题，不需要大量配图，仅保留少量关键图片素材作为视觉补充。

{override}
## 视频分析
{synthesis}

## 输出格式（纯 JSON，不用 markdown 代码块包裹）
{{{{
  "title": "标题（15-25字）",
  "hashtags": ["话题词1", "话题词2", "话题词3"],
  "original_text": "完整原片文案（{o.get('原片文案字数', '150-300字')}，纯文本，无角色对话，无标点符号，用换行分隔停顿）",
  "dialogs": [
    ["角色A", "对话内容【情绪】"],
    ["角色B", "对话内容【情绪】"]
  ],
  "images": [
    "😺 素材描述1",
    "🐶 素材描述2"
  ]
}}}}

## 对话要求
- **必须正好 {dialog_count} 轮** A/B 角色对话
- 对话结构：{o.get('对话结构', '开场几轮抛出问题 → 中间几轮给出干货建议 → 后半段深化理解 → 结尾积极号召收尾')}
- 每轮对话格式：["角色名", "对话内容【情绪标记】"]
- 情绪标记放在对话内容的末尾，用【】包裹
- 可选情绪：{emotions}
- **对话内容严禁使用任何标点符号**，用自然换行分隔表示语气停顿
- 对话要口语化、有互动感，A/B角色交替出现

## 图片素材要求（少量即可）
- {o.get('图片素材数量', 5)} 条图片素材即可（对话式脚本不需要大量配图）
- 每条格式：以 **emoji 表情**开头 + 文件名.jpg + 中文描述
- 仅用于关键场景的视觉点缀

## 原片文案要求
- {o.get('原片文案字数', '150-300字')}，{o.get('原片风格', '完整的原片叙述，纯文本，无角色对话')}
- **严禁标点符号**，用换行分隔停顿

## 图片素材要求
- **必须正好 {image_count} 条**图片素材（不能少于此数量）
- 每条格式：以 **emoji 表情**开头 + 中文描述
- 描述格式：文件名.jpg 中文描述 如 "😺 功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包"
- 用换行分隔每条素材

## 话题词要求
- 3-5个中文短语话题词
- 格式如：职场干货 #面试技巧 #求职

{ad_block}
{products}"""
