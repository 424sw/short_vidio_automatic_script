"""
AI 脚本生成：根据视频分析结果生成混剪/口播脚本。
"""
import json
import logging
import re

from openai import OpenAI

from config import (
    AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL,
    build_mix_prompt, build_oral_prompt,
    SCRIPT_GENERATION_TEMPERATURE,
)

logger = logging.getLogger(__name__)


class ScriptGeneratorError(Exception):
    """脚本生成错误."""
    pass


class ScriptGenerator:
    """脚本生成器."""

    def __init__(self):
        self._client = OpenAI(
            base_url=AGNES_BASE_URL,
            api_key=AGNES_API_KEY,
        )

    # ============================================================
    # 核心生成
    # ============================================================

    def generate(self, synthesis: str, video_title: str,
                 script_type: str = "mix",
                 custom_requirements: str = "",
                 previous_scripts: list = None,
                 variation_seed: int = 0) -> dict:
        """生成脚本.

        Args:
            synthesis: AI 视频综合分析文本
            video_title: 原视频标题
            script_type: "mix" 或 "oral"
            custom_requirements: 用户自定义要求（大白话），优先级高于默认规则
            previous_scripts: 之前生成的脚本列表（用于多样性控制）
            variation_seed: 变体编号（用于提示 AI 生成不同版本）

        Returns:
            parsed script dict
        """
        if script_type == "mix":
            prompt = build_mix_prompt(synthesis, custom_requirements)
        else:
            prompt = build_oral_prompt(synthesis, custom_requirements)

        # 注入多样性指令（多脚本模式）
        if previous_scripts:
            diversity = self._build_diversity_instruction(previous_scripts, variation_seed)
            prompt = prompt + "\n\n" + diversity

        logger.info(f"生成{script_type}脚本...")

        # 尝试让 AI 返回 JSON（最多 2 次，带内容校验反馈）
        for attempt in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=AGNES_MODEL,
                    messages=[
                        {"role": "system", "content": "你是一个专业的短视频脚本策划专家。请严格按JSON格式输出，不要用markdown代码块包裹。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4000,
                    temperature=SCRIPT_GENERATION_TEMPERATURE,
                )

                raw_text = response.choices[0].message.content.strip()
                script = self._parse_json(raw_text)

                # 结构验证（硬性要求）
                self._validate_script(script, script_type)

                # 内容验证（软性，发现问题时在首次尝试后重试）
                issues = self._validate_content(script, script_type)
                if issues and attempt == 0:
                    feedback = self._build_validation_feedback(issues)
                    prompt = prompt + "\n\n" + feedback
                    logger.warning(f"内容验证发现问题，将重试: {issues}")
                    continue  # 带反馈重试

                if issues:
                    logger.warning(f"内容验证仍有问题（已重试，接受当前结果）: {issues}")

                logger.info(f"{script_type}脚本生成成功")
                return script

            except (json.JSONDecodeError, ScriptGeneratorError) as e:
                logger.warning(f"脚本生成尝试 {attempt+1} 失败: {e}")
                if attempt == 1:
                    raise ScriptGeneratorError(
                        f"AI 脚本生成失败，返回格式异常。请重试。\n错误: {e}"
                    )

        raise ScriptGeneratorError("脚本生成失败")

    # ============================================================
    # JSON 解析
    # ============================================================

    @staticmethod
    def _parse_json(raw_text: str) -> dict:
        """从 AI 返回文本中提取 JSON.

        处理常见情况:
        - 纯 JSON
        - markdown 代码块包裹 ```json ... ```
        - 首尾有额外文字
        """
        # 去 markdown 代码块
        md_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_text)
        if md_match:
            raw_text = md_match.group(1).strip()

        # 找第一个 { 到最后一个 }
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start >= 0 and end > start:
            raw_text = raw_text[start:end + 1]

        return json.loads(raw_text)

    # ============================================================
    # 结构验证（硬性要求）
    # ============================================================

    @staticmethod
    def _validate_script(script: dict, script_type: str):
        """验证脚本基本结构."""
        from config import load_requirements
        req = load_requirements()

        if script_type == "mix":
            m = req.get("混剪", {})
            lo, _ = m.get("行数范围", [10, 16])
            if "title" not in script:
                raise ScriptGeneratorError("混剪脚本缺少 title 字段")
            if "hashtags" not in script or not isinstance(script.get("hashtags"), list):
                raise ScriptGeneratorError("混剪脚本缺少 hashtags 字段（话题词数组）")
            if "rows" not in script or not isinstance(script["rows"], list):
                raise ScriptGeneratorError("混剪脚本缺少 rows 数组")
            if len(script["rows"]) < max(lo, 5):
                raise ScriptGeneratorError(
                    f"混剪脚本行数不足: {len(script['rows'])}行（要求至少{lo}行）"
                )
            for i, row in enumerate(script["rows"]):
                if not isinstance(row, list) or len(row) < 2:
                    raise ScriptGeneratorError(f"第{i}行格式错误（需要[内容, 素材]）")

        else:  # oral
            o = req.get("口播", {})
            min_dialogs = int(o.get("对话轮数", 20) * 0.6)
            if "title" not in script:
                raise ScriptGeneratorError("口播脚本缺少 title 字段")
            if "hashtags" not in script or not isinstance(script.get("hashtags"), list):
                raise ScriptGeneratorError("口播脚本缺少 hashtags 字段（话题词数组）")
            if "original_text" not in script:
                raise ScriptGeneratorError("口播脚本缺少 original_text 字段")
            if "dialogs" not in script or not isinstance(script["dialogs"], list):
                raise ScriptGeneratorError("口播脚本缺少 dialogs 数组")
            if "images" not in script or not isinstance(script["images"], list):
                raise ScriptGeneratorError("口播脚本缺少 images 数组")
            if len(script["dialogs"]) < min_dialogs:
                raise ScriptGeneratorError(
                    f"口播脚本对话轮数不足: {len(script['dialogs'])}轮（要求至少{min_dialogs}轮）"
                )
            for i, d in enumerate(script["dialogs"]):
                if not isinstance(d, list) or len(d) < 3:
                    raise ScriptGeneratorError(f"第{i}轮对话格式错误（需要[角色, 对话, 情绪]）")

    # ============================================================
    # 内容校验（软性，发现问题时触发重试反馈）
    # ============================================================

    @staticmethod
    def _validate_content(script: dict, script_type: str) -> list:
        """对照 requirements.json 进行内容级别校验.

        返回问题描述列表。空列表表示通过。
        """
        from config import load_requirements
        req = load_requirements()
        issues = []

        if script_type == "mix":
            m = req.get("混剪", {})
            d = req.get("交付要求", {})
            title = script.get("title", "")
            hashtags = script.get("hashtags", [])
            rows = script.get("rows", [])

            # 标题字数检查
            title_spec = m.get("标题字数", "15-25字")
            try:
                parts = title_spec.replace("字", "").replace("，", ",").split("-")
                title_max = int(parts[-1].strip())
            except (ValueError, IndexError):
                title_max = 25
            if len(title) > title_max + 3:
                issues.append(f"标题过长：{len(title)}字（要求≤{title_max}字）")

            # 话题词检查
            if not hashtags or len(hashtags) < 2:
                issues.append(f"话题词不足：{len(hashtags) if hashtags else 0}个（要求至少2个）")
            elif len(hashtags) > 6:
                issues.append(f"话题词过多：{len(hashtags)}个（要求{d.get('话题词数量', '3-5个')}）")

            # 行数范围检查
            lo, hi = m.get("行数范围", [10, 16])
            if len(rows) < lo:
                issues.append(f"行数不足：{len(rows)}行（要求{lo}-{hi}行）")
            elif len(rows) > hi:
                issues.append(f"行数过多：{len(rows)}行（要求{lo}-{hi}行）")

            # 素材格式检查
            for i, row in enumerate(rows):
                if len(row) >= 2:
                    material = str(row[1])
                    if material and not re.search(r'\.(jpg|png|gif|jpeg|webp)', material, re.IGNORECASE):
                        # 只对明显不是"文件名.扩展名 描述"格式的做提示
                        if not re.match(r'^[\w一-鿿-]+\.\w{3,4}\s', material):
                            issues.append(f"第{i+1}行素材格式可能不符合要求（建议：文件名.jpg 描述）")
                            break  # 只报告一次

        else:  # oral
            o = req.get("口播", {})
            d = req.get("交付要求", {})
            title = script.get("title", "")
            hashtags = script.get("hashtags", [])
            dialogs = script.get("dialogs", [])
            images = script.get("images", [])
            target_dialogs = o.get("对话轮数", 20)
            target_images = o.get("图片素材数量", 20)
            emotion_options = set(o.get("情绪选项", []))

            # 标题字数检查
            title_spec = o.get("标题字数", "10字以内")
            try:
                title_max = int(title_spec.replace("字以内", "").strip())
            except ValueError:
                title_max = 10
            if len(title) > title_max + 3:
                issues.append(f"标题过长：{len(title)}字（要求≤{title_max}字）")

            # 话题词检查
            if not hashtags or len(hashtags) < 2:
                issues.append(f"话题词不足：{len(hashtags) if hashtags else 0}个（要求至少2个）")
            elif len(hashtags) > 6:
                issues.append(f"话题词过多：{len(hashtags)}个（要求{d.get('话题词数量', '3-5个')}）")

            # 对话轮数检查
            if len(dialogs) < target_dialogs * 0.8:
                issues.append(f"对话轮数不足：{len(dialogs)}轮（要求{target_dialogs}轮）")

            # 图片素材数量检查
            if len(images) < target_images * 0.7:
                issues.append(f"图片素材不足：{len(images)}条（要求{target_images}条）")

            # 情绪标记检查
            if emotion_options:
                for i, d in enumerate(dialogs):
                    if len(d) >= 3:
                        emotion = d[2]
                        if emotion and emotion not in emotion_options:
                            issues.append(
                                f"第{i+1}轮情绪标记'{emotion}'不在可选范围：{'/'.join(sorted(emotion_options))}"
                            )

        return issues

    @staticmethod
    def _build_validation_feedback(issues: list) -> str:
        """构建内容校验的重试反馈."""
        lines = ["\n## ⚠️ 上一版本的以下问题需要修正："]
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("请严格按以上要求重新生成 JSON。")
        return "\n".join(lines)

    @staticmethod
    def _build_diversity_instruction(previous_scripts: list, seed: int) -> str:
        """构建多样性指令（多脚本模式）."""
        lines = [
            "\n## ⚠️ 多样化要求（最高优先级）",
            "请生成一个与以下已有脚本不同的新版本。避免使用相同的素材描述、相同的叙事角度、相同的表达方式。",
            f"变体编号：{seed + 1}",
            "已有脚本摘要：",
        ]
        for i, prev in enumerate(previous_scripts):
            title = prev.get("title", f"脚本{i+1}")
            if prev.get("rows"):
                # 混剪：取前两行文案作为摘要
                snippets = [row[0][:40] for row in prev["rows"][:3] if row]
                angle = " | ".join(snippets)
                lines.append(f"- 脚本{i+1}「{title}」，角度：{angle}")
            elif prev.get("dialogs"):
                snippets = [d[1][:40] for d in prev["dialogs"][:3] if len(d) >= 2]
                angle = " | ".join(snippets)
                lines.append(f"- 脚本{i+1}「{title}」，角度：{angle}")
        lines.append("确保新脚本在素材选择、叙事角度和表达方式上与上述脚本有显著区别。")
        return "\n".join(lines)

    # ============================================================
    # 多脚本生成
    # ============================================================

    def generate_multiple(self, synthesis: str, video_title: str,
                          script_type: str, count: int,
                          custom_requirements: str = "") -> list:
        """生成多个不同版本的脚本，控制重复率 ≤40%.

        Args:
            synthesis: AI 视频综合分析文本
            video_title: 原视频标题
            script_type: "mix" 或 "oral"
            count: 生成数量（1-5）
            custom_requirements: 用户自定义要求

        Returns:
            脚本 dict 列表
        """
        scripts = []
        for i in range(count):
            logger.info(f"生成第 {i+1}/{count} 个脚本...")
            for attempt in range(3):  # 每个脚本最多 3 次尝试以确保多样性
                script = self.generate(
                    synthesis, video_title, script_type,
                    custom_requirements,
                    previous_scripts=scripts if i > 0 else None,
                    variation_seed=i,
                )
                if i == 0:
                    scripts.append(script)
                    break

                # 检查与已有脚本的重叠率
                max_overlap = max(
                    self._compute_overlap(script, prev) for prev in scripts
                )
                if max_overlap <= 0.40:
                    scripts.append(script)
                    logger.info(f"脚本 {i+1} 重叠率 {max_overlap:.0%}，通过 ✓")
                    break
                logger.warning(
                    f"脚本 {i+1} 重叠率 {max_overlap:.0%} 超过 40%，重试 ({attempt+1}/3)..."
                )
            else:
                # 3 次重试后接受当前结果
                scripts.append(script)
                logger.warning(f"脚本 {i+1} 接受当前结果（已达最大重试次数）")

        return scripts

    @staticmethod
    def _flatten_script_text(script: dict) -> str:
        """将脚本中所有文本提取为单个字符串."""
        parts = [script.get("title", "")]
        for row in script.get("rows", []):
            parts.extend(str(c) for c in row)
        parts.append(script.get("original_text", ""))
        for d in script.get("dialogs", []):
            parts.extend(str(c) for c in d)
        for img in script.get("images", []):
            parts.append(str(img))
        return " ".join(parts)

    @staticmethod
    def _compute_overlap(script_a: dict, script_b: dict) -> float:
        """计算两个脚本的 trigram Jaccard 相似度.

        Returns:
            0.0（完全不同）到 1.0（完全相同）
        """
        def trigrams(text: str) -> set:
            return set(text[i:i+3] for i in range(len(text) - 2))

        text_a = ScriptGenerator._flatten_script_text(script_a)
        text_b = ScriptGenerator._flatten_script_text(script_b)
        tri_a, tri_b = trigrams(text_a), trigrams(text_b)
        if not tri_a or not tri_b:
            return 0.0
        return len(tri_a & tri_b) / len(tri_a | tri_b)

    # ============================================================
    # 图片要求提取
    # ============================================================

    def extract_requirements_from_image(self, image_bytes: bytes,
                                         filename: str = "") -> str:
        """使用 vision 模型从上传的图片中提取脚本要求.

        Args:
            image_bytes: 图片字节数据
            filename: 文件名（用于日志）

        Returns:
            提取的中文要求文本，如无则返回空字符串
        """
        import base64

        ext = "jpeg"
        if filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
                ext = "jpeg"
            elif ext == "jpg":
                ext = "jpeg"
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        b64_uri = f"data:image/{ext};base64,{b64}"

        logger.info(f"正在识别图片中的要求: {filename}")
        try:
            response = self._client.chat.completions.create(
                model=AGNES_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": b64_uri}},
                        {"type": "text", "text": (
                            "请从这张图片中提取所有的脚本创作要求、笔记要点、格式要求。"
                            "用中文大白话总结输出，保留原文中的具体数字、格式规范和关键要求。"
                            "如果图片中没有明确的脚本要求，请回复'无'。"
                        )},
                    ],
                }],
                max_tokens=800,
                temperature=0.2,
                timeout=30,
            )
            result = response.choices[0].message.content.strip()
            if result == "无" or not result:
                logger.info(f"图片中未识别到脚本要求: {filename}")
                return ""
            logger.info(f"从图片中识别到要求 ({len(result)}字): {result[:80]}...")
            return result
        except Exception as e:
            logger.warning(f"图片要求提取失败: {e}")
            raise ScriptGeneratorError(f"图片识别失败: {e}")

    # ============================================================
    # 脚本类型自动检测
    # ============================================================

    def detect_type(self, synthesis: str, video_title: str) -> str:
        """根据视频内容自动检测脚本类型.

        图文类/知识分享类 → 混剪
        剧情类/对话类/真人出镜 → 口播
        """
        prompt = f"""分析以下视频内容，判断它更适合制作"混剪脚本"还是"口播脚本"。

视频标题: {video_title}

视频综合分析:
{synthesis[:2000]}

判断标准:
- 混剪: 适合图文+配音形式，如知识分享、观点输出、干货盘点、情感文案等
- 口播: 适合真人出镜+对话/独白形式，如剧情、角色对话、采访、Vlog等

只回复一个词: "mix" 或 "oral"."""

        try:
            response = self._client.chat.completions.create(
                model=AGNES_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.3,
            )
            answer = response.choices[0].message.content.strip().lower()
            if "oral" in answer or "口播" in answer:
                return "oral"
            return "mix"
        except Exception as e:
            logger.warning(f"脚本类型检测失败，默认使用混剪: {e}")
            return "mix"
