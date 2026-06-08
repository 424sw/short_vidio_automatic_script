"""Prompt 构建：视频分析 + 混剪/口播脚本生成 + 审核微调。"""
import json
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


def get_quality_config(quality: str = "standard") -> dict:
    """质量配置：standard / fine 两档。

    standard → int8 + beam=5（更快）；fine → float32 + beam=10（更准）。
    """
    presets = {
        "standard": {
            "label": "标准",
            "compute_type": "int8",
            "beam_size": 5,
            "est_asr_time": "约 60-120 秒",
        },
        "fine": {
            "label": "精细",
            "compute_type": "float32",
            "beam_size": 10,
            "est_asr_time": "约 90-180 秒",
        },
    }
    return presets.get(quality, presets["standard"])


def build_synthesis_prompt(video_title: str, audio_transcript: str = "") -> str:
    """构建视频综合分析的 Prompt（基于音频转录，不分析视频帧）。"""
    if audio_transcript:
        audio_inst = (
            "根据音频转录文字，还原视频完整口播文案，标注时间节点。\n\n"
            "## 🔴 转录纠错（重要）\n"
            "音频转录由机器自动生成，**必然存在识别错误**（尤其是网络热词、品牌名、行业术语）。"
            "你需要**修复以下典型错误**：\n"
            "- 音近错字：如「主包」应为「主播」、「加入们」应为「家人们」\n"
            "- 品牌名错误：如「渔泡直聘」应为「鱼泡直聘」\n"
            "- 中英混杂词拆错：如「offer」被识别为「哦分」、「yyds」被识别为「歪歪地爱思」\n"
            "- 网络用语识别失败：如「芭比Q」被识别为「爸爸抠」、「栓Q」被识别为「专注抠」\n"
            "- 断句混乱导致语义断裂\n\n"
            f"音频转录：\n{audio_transcript}"
        )
    else:
        audio_inst = "尝试推测视频的口播文案结构。"

    transcript_note = "（附有语音转录文字）" if audio_transcript else ""
    return f"""你是短视频内容分析师。请分析视频"{video_title}"{transcript_note}。

## 一、视频结构
开头钩子 → 中间展开 → 结尾总结（标注时间线）

## 二、完整口播文案
{audio_inst}

## 三、风格特点
节奏、情绪基调、语言风格

## 四、关键信息点
5-8 个核心信息点"""


def _build_diversity_instruction(seed: int, script_type: str) -> str:
    """根据 seed 生成多样性指令，注入 Prompt 以使多个脚本差异明显。

    每个 seed 对应不同的叙事角度、素材风格，避免重复。
    """
    if seed <= 0:
        return ""

    # 不同 seed → 不同叙事角度
    angles_mix = [
        "**痛点共鸣型**：从读者的真实焦虑/困境切入（如「投了几十份简历石沉大海」），先共情再给方案。",
        "**反转对比型**：先描述大家都知道的「表面现象」，再抛出「但你知道吗……」的反转真相。",
        "**干货清单型**：以「X个秘诀/X个坑」的清单体展开，节奏明快，信息密度高。",
        "**故事带入型**：用一个具体的场景/人物故事开头，让读者代入角色经历。",
        "**数据震撼型**：以具体数字/比例开场制造冲击力（如「87%的求职者都忽略了这一点」）。",
    ]
    angles_oral = [
        "**解惑型**：A 有困惑/误解，B 一步步化解，层层递进。",
        "**辩论型**：A/B 观点有分歧，互相切磋，最后达成共识。",
        "**故事分享型**：B 讲述亲身经历/所见所闻，A 追问细节、感同身受。",
        "**干货教学型**：B 是「老师」角色，A 是「学生」角色，一问一答间传授知识。",
        "**闲聊切入型**：从轻松的日常话题慢慢过渡到核心内容，自然不生硬。",
    ]
    angles = angles_oral if script_type == "oral" else angles_mix
    angle = angles[seed % len(angles)]

    # 不同 seed → 不同素材偏好
    animal_options = ["猫", "狗", "熊猫", "兔子", "仓鼠", "鸭子", "鹦鹉", "金毛"]
    meme_styles = ["经典表情包", "影视剧截图", "动漫表情", "沙雕图", "萌宠图"]

    if script_type == "oral":
        return f"""
## 🔀 多样性指令（版本 {seed}）
- 本版本的对话叙事风格为{angle}
- 每个角色的话语风格、句式、口头禅应与其他版本有明显区分
- dialogue 的具体措辞、比喻、举例必须不同"""
    else:
        animal = animal_options[seed % len(animal_options)]
        meme = meme_styles[(seed * 3) % len(meme_styles)]
        return f"""
## 🔀 多样性指令（版本 {seed}）
- 本版本的叙事角度为{angle}
- 文案的**开头钩子**、**中间展开节奏**、**结尾方式**应与标准版本有明显差异
- 素材偏好：多使用「{animal}」类素材和「{meme}」风格
- 具体措辞、举例、比喻必须与标准版本不同"""


def build_mix_prompt(synthesis: str,
                      audio_transcript: str = "", variation_seed: int = 0,
                      target_chars: int = 0) -> str:
    """构建混剪脚本生成的 Prompt。

    强制执行规则：
    - 3-5 个话题词
    - 软广在前50%行首次出现，是第一个被谈及的品牌
    - 软广后紧跟产品介绍库中匹配的文案
    - 无标点符号，靠换行分隔停顿
    - target_chars > 0 时，强制匹配原视频口播字数
    """
    req = load_requirements()
    m = req["混剪"]
    lo, hi = m["行数范围"]
    ad = m.get("广告", {})
    brand = ad.get("品牌", "鱼泡直聘")
    _ad_position = ad.get("位置", "约前50%位置处")  # 保留读取，用于对齐配置
    products = _build_product_section()
    mid_point = hi // 2

    # ==== 内容长度约束（硬性） ====
    length_constraint = ""
    if target_chars > 0:
        lo_chars = int(target_chars * 1.1)
        hi_chars = int(target_chars * 1.2)
        length_constraint = (
            f"\n## 🔴 内容长度硬性约束\n"
            f"原视频口播约 **{target_chars} 字**。\n"
            f"你的 rows 中所有文案的字数总和 **严禁超过 {hi_chars} 字**，也**不得少于 {lo_chars} 字**。\n"
            f"生成完逐行统计字数，超出则删减内容，不足则补充。\n"
            f"**不遵守此约束 = 不合格，必须重做。**"
        )
    else:
        length_constraint = (
            "\n## 🔴 内容长度约束\n"
            "参考原视频的长度和节奏，脚本内容应与参考视频篇幅相当。\n"
            "生成前先估算原视频的大致字数，以该字数为基准输出，不要大幅超出。"
        )

    # ==== 广告植入指令（严格） ====
    ad_lines = [
        "## 🔴 广告植入（硬性要求，必须全部满足）",
        f"1. 品牌：**{brand}**（软广植入）",
        f"2. 必须在脚本的**前50%行**（即第1行到第{mid_point}行之间）首次提及「{brand}」",
        f"3. 「{brand}」必须是整个脚本中**第一个**被谈及的商业品牌/产品（不能先提其他品牌再提{brand}）",
        f"4. 提及「{brand}」后，必须紧接着从下方📦产品介绍库中，根据视频主题选择**最匹配**的一段文案（约20-40字），格式为：「{brand}，[选中的产品介绍文案]」",
    ]
    ad_block = "\n".join(ad_lines)

    diversity = _build_diversity_instruction(variation_seed, "mix")

    return f"""你是短视频脚本策划。生成混剪脚本（**单人图文讲解**风格）。

视频画面 + 单人旁白口播 + 趣味素材穿插，每条口播文案配一张对应的素材图片。

{diversity}
{length_constraint}
## 视频分析
{synthesis}

## 输出格式（纯 JSON，不用 markdown 代码块包裹）

格式说明：rows 中每条文案是一段完整的口播内容，用 \\\\n 分隔意群表示语气停顿。**每行 1-3 句**均可，严禁超过 3 句。

正确示例（注意行间句数不一，有的 2 句有的 3 句，不要每行都凑 3 句）：
{{{{
  "title": "零基础转行面试三大秘诀 #转行求职 #面试技巧 #零基础",
  "hashtags": ["转行求职", "面试技巧", "零基础", "职场干货"],
  "rows": [
    ["你是不是也像我一样\\n投了几十份简历\\n一个面试都没有", "功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包"],
    ["朋友推荐我上鱼泡直聘\\n上面都是真实直招岗位\\n零基础也能快速上手", "小狗点头.jpg 小狗戴着眼镜在键盘前点头的表情包"]
  ]
}}}}

## 脚本要求
- 共 {lo}-{hi} 行，每行 = 一段完整口播文案（1-3句，用换行分隔）+ 一张配图素材
- 🔴 **内容长度与参考视频相当**：仿写内容的总篇幅（字数、行数、信息量）应与上方视频分析中的原视频文案长度一致，不要大幅超出或缩水
- 风格：**单人讲解**，口语化，仿佛对着镜头跟观众聊天
- 🔴 **断句控制（硬性）**：每行文案 **1-3 句**，用 \\\\n 分隔。严禁 4 句及以上。**不要每行都凑到 3 句**——有的一两句能说清就用一两句，需要展开的才用 3 句。过多断句=画面切换过于频繁。
- 🔴 **人机感（硬性）**：像真人说话，不要写成文章。大量用口语词（呢、吧、啊、嘛），多用反问和感叹，句式长短错落（短句2-4字+长句15-25字交替），严禁「此外/因此/综上所述/值得注意的是」等公文套话
- 🔴 **措辞差异化**：核心观点可一致，但具体措辞、举例、比喻必须与参考视频不同，不得大段照搬原文
- 素材格式：{m.get('素材格式', '文件名.jpg 中文描述')}
- 素材风格：{m.get('素材风格', '尽量使用动物、表情包等趣味素材')}，每条素材与对应文案内容匹配
- 🔴 话题词：**必须正好 4-5 个**独立的中文短语，如：职场干货、面试技巧、求职指南、零基础转行
- 🔴 少于 4 个话题词会被拒绝，严禁只写 2-3 个
- 🔴 **话题词中严禁出现品牌名**（如：鱼泡直聘、BOSS直聘等），话题词是内容标签，不是广告位

## 标题要求
- 标题字数：{m.get('标题字数', '15-25字')}
- 🔴 **标题本身必须包含#话题词**，如：「零基础转行面试三大秘诀 #职场干货 #面试技巧」
- hashtags 数组中仍需单独列出话题词

{ad_block}
{products}"""


def build_oral_prompt(synthesis: str, audio_transcript: str = "",
                      variation_seed: int = 0, target_chars: int = 0) -> str:
    """构建口播脚本生成的 Prompt。

    强制执行规则：
    - 正好 20 轮对话
    - 正好 20 条图片素材
    - 每轮对话【情绪标记】
    - 软广在前50%对话轮首次出现
    - target_chars > 0 时，强制匹配原视频口播字数
    """
    req = load_requirements()
    o = req["口播"]
    dia_range = o.get("对话轮数范围", [8, 20])
    dia_lo, dia_hi = dia_range[0], dia_range[1]
    # 根据参考视频长度计算建议轮数：每15字≈1轮对话
    if target_chars > 0:
        suggested = max(dia_lo, min(dia_hi, target_chars // 15))
    else:
        suggested = (dia_lo + dia_hi) // 2
    mid = suggested // 2
    emotions = "、".join(o.get("情绪选项", []))
    products = _build_product_section()

    # ==== 内容长度约束（硬性） ====
    length_constraint = ""
    if target_chars > 0:
        lo_chars = int(target_chars * 1.1)
        hi_chars = int(target_chars * 1.2)
        length_constraint = (
            f"\n## 🔴 内容长度硬性约束\n"
            f"原视频口播约 **{target_chars} 字**。\n"
            f"- **original_text（原片文案）**：不做字数限制，**完整还原**参考视频的全部口播文案即可\n"
            f"- **dialogs（A/B 对话脚本）**：所有对话字数总和在 **{lo_chars}~{hi_chars} 字**之间，与参考视频篇幅匹配\n"
            f"生成完统计 dialogs 总字数，超出必须删减，不足可以适当补充。**不遵守此约束 = 不合格，必须重做。**"
        )
    else:
        length_constraint = (
            "\n## 🔴 内容长度约束\n"
            "参考原视频的长度和节奏，dialogs 脚本内容应与参考视频篇幅相当。\n"
            "original_text 不限字数，完整还原参考视频全部口播文案。"
        )

    # ==== 广告植入指令（严格） ====
    ad_lines = [
        "## 🔴 广告植入（硬性要求，必须全部满足）",
        "1. 品牌：**鱼泡直聘**（软广植入）",
        f"2. 必须在对话的**前50%轮次**（即第1轮到第{mid}轮之间），由某个角色首次提及「鱼泡直聘」",
        "3. 「鱼泡直聘」必须是整个脚本中**第一个**被谈及的商业品牌/产品",
        "4. 提及「鱼泡直聘」后，该角色必须紧接着从下方📦产品介绍库中选择**最匹配**的一段文案（约20-40字），自然地融入对话",
        "5. 广告内容的情绪标记应为【推荐】",
    ]
    ad_block = "\n".join(ad_lines)

    diversity = _build_diversity_instruction(variation_seed, "oral")

    audio_section = ""
    if audio_transcript:
        audio_section = f"""## 🔴 原始音频转录（参考材料）
下面是语音转文字得到的原始音频内容，**必然存在识别错误**（机器 ASR 对网络热词、品牌名、中英混杂词的识别率很低）。
你需要结合音频转录上下文，**逐句修复并还原**出完整、逻辑连贯的视频口播文案。
🔴 若转录含繁体字，**必须全部转为简体中文**。
**修复重点**（这些是 ASR 最常犯的错误）：
- 音近错字：如「主包」→「主播」、「加入们」→「家人们」、「渔泡」→「鱼泡」
- 中英混杂词拆错：如「哦分」→「offer」、「歪歪地爱思」→「yyds」
- 网络用语识别失败：如「爸爸抠」→「芭比Q」、「专注抠」→「栓Q」
- 断句异常：把一句话拆成碎片，或把多句黏在一起
- 品牌名/专有名词写错
- 最终 original_text 应该是**一段逻辑清晰、可独立阅读的完整文案**，不是对原始转录的照抄
{audio_transcript}"""
    else:
        audio_section = "## ⚠️ 无音频转录\n本视频无语音转文字内容，请仅根据视频标题和分析上下文来创作 original_text。"

    return f"""你是短视频脚本策划。生成口播脚本（**A/B角色对话**风格）。

两个角色通过对话形式讨论话题，不需要大量配图，仅保留少量关键图片素材作为视觉补充。

## 🔴🔴 核心原则：仿写，不是扩写！
- 你的任务是**模仿**参考视频的风格、结构、信息密度来创作一个**新**脚本
- **不是**把参考视频的内容拉长、展开、润色（那是扩写，会被拒绝）
- 参考视频说多少字，你就说多少字。参考视频用多快的节奏，你就用多快的节奏
- 核心观点可以一致，但**具体措辞、举例、比喻、句式必须完全不同**
- 把自己想象成：看了这个视频后，用自己的话复述一遍，而不是对着原文做 paraphrase

{audio_section}

{diversity}
{length_constraint}
## 视频分析
{synthesis}

## 输出格式（纯 JSON，不用 markdown 代码块包裹）
{{{{
  "title": "标题（15-25字，纯标题文本，不含#话题词）",
  "hashtags": ["话题词1", "话题词2", "话题词3", "话题词4"],
  "original_text": "根据音频转录和上下文还原的完整口播文案，逻辑通顺、无识别错误",
  "dialogs": [
    ["A", "多句话的完整对话内容，不能只有一句【4字标记】"],
    ["B", "多句话的完整对话内容，不能只有一句【4字标记】"]
  ],
  "images": [
    "鱼泡直聘APP图标.jpg 蓝白配色求职招聘应用图标"
  ]
}}}}

## 对话要求
- **必须正好 {suggested} 轮左右（{dia_lo}-{dia_hi} 之间）A/B 角色对话
- 对话结构：{o.get('对话结构', '开场几轮抛出问题 → 中间几轮给出干货建议 → 后半段深化理解 → 结尾积极号召收尾')}
- 🔴 角色名**只能是 "A" 和 "B"**（纯字母），**绝对不能**写成 "角色A"、"角色B"、"**A**" 等任何变体
- 🔴 **每轮对话必须是 3-5 句话的完整大段表达**，包含观点、铺垫、回应、递进，**严禁**单句对话（单句是不合格的输出）
- 🔴 **对话内容必须覆盖原文所有关键信息点**，但用**不同的措辞和表述方式**，不要照搬 original_text 的句子结构
- 🔴🔴 **每轮对话末尾必须有【标记】**：用【】包裹 4 字的情绪/动作/描述词（如【恍然大悟】【好奇追问】【无奈摇头】【真诚推荐】），**没有标记的对话会被验证步骤直接拒绝**
- 可选标记可参考：{emotions}
- **对话内容用标点符号正常断句**（口播是对白，需标点表达语气停顿）
- 对话要口语化、有互动感，A/B角色交替出现

## 🔴 人机感要求（硬性）
- **像真人聊天，不要写成文章**：大量使用口语词（呢、吧、啊、嘛、哦、啦、呗），多用反问、感叹、语气词
- **严禁书面语/官方腔**：不要用「此外」「因此」「综上所述」「值得注意的是」「由此可见」等公文套话
- **句式长短错落**：短句 2-4 字 + 长句 15-25 字交替，不要每句都工整对称
- **有真实互动感**：角色之间有追问、打断、认同、质疑等自然互动，不要像在念稿
- **措辞差异化**：你的文案和参考视频的核心观点可以一致，但具体措辞、举例、比喻必须不同，不得大段照搬原文

## 🔴 标题要求
- 标题 15-25 字，**纯标题文本，不包含 #话题词**
- 话题词单独放在 hashtags 数组中

## 图片素材要求
- 🔴 **只需 2-3 条**图片素材（文字描述即可，系统不支持插入实际图片）
- 素材风格：**官方/品牌风格**，如 APP 图标、产品截图、场景示意图、数据图表等
- 🔴 **严禁**使用表情包、emoji、动物、卡通等趣味素材（那是混剪脚本才用的风格）
- 每条格式：中文描述，如 "鱼泡直聘APP图标.jpg 蓝白配色求职招聘应用图标"
- 用换行分隔每条素材

## 话题词要求
- 4-5个中文短语话题词
- 格式如：职场干货 #面试技巧 #求职

## 🔴 写后自查（输出前逐项确认）
- [ ] dialogs 总字数是否与参考视频字数在同一量级？超出即删（original_text 不限字数，完整还原即可）
- [ ] 每轮对话末尾是否都有【标记】？
- [ ] 措辞是否与原文明显不同？（逐句对比，相似则改）
- [ ] 对话是否像真人聊天？（有语气词、有互动、不书面）
- [ ] 标题是否不含 #话题词？

{ad_block}
{products}"""


def build_review_prompt(script_json: dict, synthesis: str = "",
                        target_chars: int = 0, similarity: float = 0.0,
                        script_chars: int = 0) -> str:
    """审核微调 Prompt：**只关注两项** —— ①内容长度匹配 ②内容相似度匹配。

    格式问题（标记/角色名/话题词/对话轮数等）由 _validate() + _fix_markers() 兜底，
    不在此处理。
    """
    script_text = json.dumps(script_json, ensure_ascii=False, indent=2)

    # ---- 诊断：仅长度 + 相似度 ----
    diagnoses = []
    instructions = []

    if target_chars > 0 and script_chars > 0:
        ratio = script_chars / target_chars
        lo_chars = int(target_chars * 1.1)
        hi_chars = int(target_chars * 1.2)
        if ratio > 1.2:
            diagnoses.append(f"过长：{script_chars}字 vs 参考{target_chars}字（+{int((ratio-1)*100)}%）")
            instructions.append(
                f"**缩写+仿写**：压缩到 {lo_chars}~{hi_chars} 字。删冗余、并同类、换措辞降相似度。")
        elif ratio < 1.1:
            diagnoses.append(f"过短：{script_chars}字 vs 参考{target_chars}字（{int(ratio*100)}%）")
            instructions.append(
                f"**扩写+仿写**：扩充到 {lo_chars}~{hi_chars} 字。补细节、丰互动、换措辞降相似度。")

    if similarity > 0.4:
        diagnoses.append(f"相似度过高：{similarity*100:.0f}%（上限40%）")
        instructions.append(
            "**降重仿写（硬性）**：以下 3 条必须全部做到——\n"
            "① 换句式：把陈述句改成反问/感叹/假设句（\"你知道吗？\"\"要是...呢？\"\"想想看！\"）\n"
            "② 换案例：把原文的具体数据、故事、举例全部换成你自己创造的同类素材\n"
            "③ 换措辞：同一个意思用完全不同的词来表达（如\"找工作\"→\"谋职\"→\"上岸\"→\"拿offer\"轮换）\n"
            "目标：改完后相似度 < 40%，不能只是换几个同义词敷衍。")

    diagnosis_text = "\n".join(f"- {d}" for d in diagnoses) if diagnoses else "✅ 长度与相似度均在合理范围"
    instruction_text = "\n".join(f"{i+1}. {instr}" for i, instr in enumerate(instructions))

    # 达标 → 原样返回
    if not instructions:
        return f"""以下脚本已通过审核（长度和相似度均达标）。**直接原样输出，不做任何修改。**

```json
{script_text}
```
纯 JSON，无 markdown 包裹。"""

    # 有诊断 → 二次仿写
    return f"""你是短视频脚本策划，之前输出的脚本在长度或相似度上不达标，需要做一次**二次仿写**。

## 参考视频分析
{synthesis[:1500] if synthesis else "（无）"}

## 当前脚本
```json
{script_text}
```

## 🔴 诊断
{diagnosis_text}

## 🔴 修改指令
{instruction_text}

## 要求
- 保持 JSON 结构（字段、类型）完全不变
- 🔴 **original_text 一个字都不许改**，它是原视频文案的忠实还原，不是创作内容
- 只调整 **dialogs（对话）** 的篇幅和措辞，降低与参考视频的相似度

## 输出
纯 JSON，无 markdown 包裹。"""