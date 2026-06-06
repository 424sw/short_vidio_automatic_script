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
    """质量配置：fast / standard / fine 三档。

    影响抽帧数量上限和逐帧描述详细程度。
    """
    presets = {
        "fast": {
            "label": "快速",
            "max_frames": 5,
            "est_time": "约 30-60 秒",
            "vision_detail": "简要描述画面关键元素、文字、人物",
        },
        "standard": {
            "label": "标准",
            "max_frames": 10,
            "est_time": "约 1-2 分钟",
            "vision_detail": "详细描述画面内容、文字、构图",
        },
        "fine": {
            "label": "精细",
            "max_frames": 20,
            "est_time": "约 2-4 分钟",
            "vision_detail": "尽可能详细地描述所有可见细节，包括人物微表情、画面色调、字体样式、构图逻辑",
        },
    }
    return presets.get(quality, presets["standard"])


def build_synthesis_prompt(frame_count: int, video_title: str,
                           descriptions: str, audio_transcript: str = "") -> str:
    """构建视频综合分析的 Prompt."""
    if audio_transcript:
        audio_hint = "，以及音频转录文字"
        audio_inst = (
            "根据音频转录文字和画面内容，还原视频完整口播文案，标注时间节点。\n\n"
            "## 🔴 转录纠错（重要）\n"
            "音频转录由机器自动生成，**必然存在识别错误**（尤其是网络热词、品牌名、行业术语）。"
            "你需要结合画面帧内容和上下文，**修复以下典型错误**：\n"
            "- 音近错字：如「主包」应为「主播」、「加入们」应为「家人们」\n"
            "- 品牌名错误：如「渔泡直聘」应为「鱼泡直聘」\n"
            "- 中英混杂词拆错：如「offer」被识别为「哦分」、「yyds」被识别为「歪歪地爱思」\n"
            "- 网络用语识别失败：如「芭比Q」被识别为「爸爸抠」、「栓Q」被识别为「专注抠」\n"
            "- 断句混乱导致语义断裂\n\n"
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


def build_mix_prompt(synthesis: str, custom_requirements: str = "",
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
        lo_chars = int(target_chars * 0.8)
        hi_chars = int(target_chars * 1.2)
        length_constraint = (
            f"\n## 🔴 内容长度硬性约束\n"
            f"原视频口播约 **{target_chars} 字**。"
            f"你的 rows 中所有文案的字数总和必须在 **{lo_chars}-{hi_chars} 字**之间。"
            f"不要大幅超出或缩水，保持在原视频篇幅的 80%-120% 范围内。"
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

    override = ""
    if custom_requirements and custom_requirements.strip():
        override = f"\n## 用户自定义要求（最高优先级）\n{custom_requirements.strip()}\n"

    diversity = _build_diversity_instruction(variation_seed, "mix")

    return f"""你是短视频脚本策划。生成混剪脚本（**单人图文讲解**风格）。

视频画面 + 单人旁白口播 + 趣味素材穿插，每条口播文案配一张对应的素材图片。

{override}
{diversity}
{length_constraint}
## 视频分析
{synthesis}

## 输出格式（纯 JSON，不用 markdown 代码块包裹）

格式说明：rows 中每条文案是一段完整的口播内容（不是一句话），用 \\\\n 在适当位置换行来分隔意群、代替标点，表示语气停顿。

正确示例（注意文案内容中的 \\\\n 换行）：
{{{{
  "title": "零基础转行面试三大秘诀 #转行求职 #面试技巧 #零基础",
  "hashtags": ["转行求职", "面试技巧", "零基础", "职场干货"],
  "rows": [
    ["你是不是也像我一样\\n投了几十份简历\\n一个面试都没有", "功德猫.jpg 穿僧袍戴佛珠的猫咪祈福表情包"],
    ["朋友推荐我上鱼泡直聘\\n上面岗位都是真实直招\\n而且还有新人培训\\n零基础也能快速上手", "小狗点头.jpg 小狗戴着眼镜在键盘前点头的表情包"]
  ]
}}}}

## 脚本要求
- 共 {lo}-{hi} 行，每行 = 一段完整口播文案（多句话，用换行分隔）+ 一张配图素材
- 🔴 **内容长度与参考视频相当**：仿写内容的总篇幅（字数、行数、信息量）应与上方视频分析中的原视频文案长度一致，不要大幅超出或缩水
- 风格：**单人讲解**，口语化，仿佛对着镜头跟观众聊天
- 每行文案必须是**一段完整的口语内容**（2-5句话），用自然换行（\\\\n）分隔来表示语言停顿，严禁任何标点符号
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


def build_oral_prompt(synthesis: str, custom_requirements: str = "",
                      audio_transcript: str = "", variation_seed: int = 0,
                      target_chars: int = 0) -> str:
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
    dialog_count = o.get("对话轮数", 20)
    emotions = "、".join(o.get("情绪选项", []))
    products = _build_product_section()
    mid_dialog = dialog_count // 2

    # ==== 内容长度约束（硬性） ====
    length_constraint = ""
    if target_chars > 0:
        lo_chars = int(target_chars * 0.8)
        hi_chars = int(target_chars * 1.2)
        length_constraint = (
            f"\n## 🔴 内容长度硬性约束\n"
            f"原视频口播约 **{target_chars} 字**。"
            f"你的 original_text 字数必须在 **{lo_chars}-{hi_chars} 字**之间，"
            f"dialogs 中所有对话的字数总和也应在 **{lo_chars}-{hi_chars} 字**之间。"
            f"不要大幅超出或缩水，保持在原视频篇幅的 80%-120% 范围内。"
        )

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

    diversity = _build_diversity_instruction(variation_seed, "oral")

    audio_section = ""
    if audio_transcript:
        audio_section = f"""## 🔴 原始音频转录（参考材料）
下面是语音转文字得到的原始音频内容，**必然存在识别错误**（机器 ASR 对网络热词、品牌名、中英混杂词的识别率很低）。
你需要结合画面帧分析，**逐句修复并还原**出完整、逻辑连贯的视频口播文案。
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
        audio_section = "## ⚠️ 无音频转录\n本视频无语音转文字内容，请仅根据画面帧分析来创作 original_text。"

    return f"""你是短视频脚本策划。生成口播脚本（**A/B角色对话**风格）。

两个角色通过对话形式讨论话题，不需要大量配图，仅保留少量关键图片素材作为视觉补充。

{audio_section}

{override}
{diversity}
{length_constraint}
## 视频分析
{synthesis}

## 输出格式（纯 JSON，不用 markdown 代码块包裹）
{{{{
  "title": "标题（15-25字）",
  "hashtags": ["话题词1", "话题词2", "话题词3", "话题词4"],
  "original_text": "根据音频转录和帧分析还原的完整口播文案，逻辑通顺、无识别错误",
  "dialogs": [
    ["A", "多句话的完整对话内容，不能只有一句【4字标记】"],
    ["B", "多句话的完整对话内容，不能只有一句【4字标记】"]
  ],
  "images": [
    "鱼泡直聘APP图标.jpg 蓝白配色求职招聘应用图标"
  ]
}}}}

## 对话要求
- **必须正好 {dialog_count} 轮** A/B 角色对话
- 对话结构：{o.get('对话结构', '开场几轮抛出问题 → 中间几轮给出干货建议 → 后半段深化理解 → 结尾积极号召收尾')}
- 🔴 角色名**只能是 "A" 和 "B"**（纯字母），**绝对不能**写成 "角色A"、"角色B"、"**A**" 等任何变体
- 🔴 **每轮对话必须是 3-5 句话的完整大段表达**，包含观点、铺垫、回应、递进，**严禁**单句对话（单句是不合格的输出）
- 🔴 **对话内容必须基于 original_text 展开**，覆盖原文所有关键信息点，不要凭空编造
- 情绪标记放在每轮对话内容的末尾，用【】包裹
- 🔴 **标记以 4 字为主**（如：【热心推荐】【恍然大悟】【疑惑不解】【无奈摇头】），可以是动作、情绪、描述，不限于情绪词
- 可选标记可参考：{emotions}
- **对话内容用标点符号正常断句**（口播是对白，需标点表达语气停顿）
- 对话要口语化、有互动感，A/B角色交替出现

## 图片素材要求
- 🔴 **只需 2-3 条**图片素材（文字描述即可，系统不支持插入实际图片）
- 素材风格：**官方/品牌风格**，如 APP 图标、产品截图、场景示意图、数据图表等
- 🔴 **严禁**使用表情包、emoji、动物、卡通等趣味素材（那是混剪脚本才用的风格）
- 每条格式：中文描述，如 "鱼泡直聘APP图标.jpg 蓝白配色求职招聘应用图标"
- 用换行分隔每条素材

## 话题词要求
- 4-5个中文短语话题词
- 格式如：职场干货 #面试技巧 #求职

{ad_block}
{products}"""


def build_review_prompt(script_json: dict, script_type: str,
                        original_prompt: str) -> str:
    """构建 AI 审核微调的 Prompt。

    将已生成的脚本 JSON 连同原始生成 Prompt 回传给 AI，
    要求逐项对照校验，自动修正格式偏差和内容缺失。
    """
    req = load_requirements()
    script_text = json.dumps(script_json, ensure_ascii=False, indent=2)

    if script_type == "oral":
        o = req.get("口播", {})
        dialog_count = o.get("对话轮数", 20)
        checklist = f"""## 🔴 逐项审核清单（口播脚本）

1. **标题**：15-25字，不包含话题词
2. **hashtags**：正好 4-5 个中文短语，**严禁包含品牌名**（如鱼泡直聘）
3. **original_text**：完整、通顺、逻辑连贯的文案，无 ASR 识别错误（如「主包→主播」）
4. **dialogs**：
   - 必须正好 {dialog_count} 轮
   - 角色名只能写 "A" 和 "B"（纯字母），不是「角色A」「**A**」
   - 每轮 3-5 句完整大段对话，严禁单句敷衍
   - 每轮末尾有【4字标记】（如【热心推荐】【恍然大悟】）
   - 对话覆盖 original_text 所有关键信息点
   - 使用标点符号正常断句
5. **images**：只需 2-3 条，官方/品牌风格（APP图标、截图、数据图表），**严禁**表情包/动物/卡通
6. **广告植入**：
   - 品牌「鱼泡直聘」必须在前50%轮次（1~{dialog_count // 2}轮）首次出现
   - 必须是第一个被谈及的商业品牌
   - 品牌名后紧跟一段产品介绍库中的匹配文案（20-40字）
   - 广告内容的情绪标记为【推荐】"""
    else:
        m = req.get("混剪", {})
        lo, hi = m.get("行数范围", [10, 16])
        checklist = f"""## 🔴 逐项审核清单（混剪脚本）

1. **标题**：15-25字，**必须包含 #话题词**（如「零基础转行面试三大秘诀 #职场干货 #面试技巧」）
2. **hashtags**：正好 4-5 个中文短语，**严禁包含品牌名**（如鱼泡直聘）
3. **rows**：在 [{lo}, {hi}] 行范围内
4. **每行** = [口播文案, 素材描述]，文案用 \\\\n 换行替代标点，**严禁任何标点符号**
5. **素材**：动物/表情包等趣味风格，与文案内容匹配
6. **广告植入**：
   - 品牌「鱼泡直聘」必须在前50%行（1~{hi // 2}行）首次出现
   - 必须是第一个被谈及的商业品牌
   - 品牌名后紧跟一段产品介绍库中的匹配文案（20-40字）
7. **内容篇幅**：与上方视频分析中的原视频文案长度一致，不大幅超出或缩水"""

    return f"""你是短视频脚本质量审核员。请根据以下要求，逐项审核并修正脚本。

## 原始生成要求
{original_prompt}

## 当前生成的脚本
```json
{script_text}
```

{checklist}

## 输出要求
- 返回修正后的**完整 JSON**（格式与原始生成一致）
- **只输出纯 JSON**，不输出任何解释、说明、markdown 包裹
- 如果某项已满足要求，保持原样不动；如果某项未满足，直接修正
- **严禁**新增或删除脚本中的关键结构字段"""