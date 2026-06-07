"""AI 脚本生成：根据视频分析结果生成混剪/口播脚本。"""
import json
import logging
import re
import time
from openai import OpenAI
from config import AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL, load_requirements
from src.prompt_builder import build_mix_prompt, build_oral_prompt, build_review_prompt

logger = logging.getLogger(__name__)


class ScriptGeneratorError(Exception):
    pass


class ScriptGenerator:

    def __init__(self):
        self._client = OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY,
                             timeout=120.0)

    def generate(self, synthesis: str, script_type: str = "mix",
                 video_title: str = "", audio_transcript: str = "",
                 variation_seed: int = 0, target_chars: int = 0) -> dict:
        """生成脚本。一次 AI 调用 + 最多一次 JSON 格式修复，不做内容校验。

        格式问题交给 review() 审核微调步骤统一处理，避免重复生成浪费时间。
        """
        if script_type == "oral":
            prompt = build_oral_prompt(synthesis, audio_transcript=audio_transcript,
                                       variation_seed=variation_seed,
                                       target_chars=target_chars)
        else:
            prompt = build_mix_prompt(synthesis, audio_transcript=audio_transcript,
                                      variation_seed=variation_seed,
                                      target_chars=target_chars)

        type_label = "口播" if script_type == "oral" else "混剪"
        src = f"（来源: {video_title}）" if video_title else ""
        logger.info("生成%s脚本%s...", type_label, src)

        # 一次 AI 调用
        raw = self._call_api(prompt, target_chars=target_chars)

        # 尝试解析 JSON，最多一次轻量修复（只修格式，不重生成内容）
        try:
            script = self._parse_json(raw)
        except ScriptGeneratorError:
            logger.warning("JSON 解析失败，尝试一次轻量修复...")
            raw2 = self._call_api(
                "你上次输出的内容 JSON 格式有误，无法解析。"
                "请严格按格式重新输出纯 JSON，不要用 markdown 代码块包裹，"
                "确保所有字符串用双引号，没有尾随逗号。")
            script = self._parse_json(raw2)

        logger.info("%s脚本生成成功（由审核步骤负责质量检查）", type_label)
        return script

    def generate_multiple(self, synthesis: str, script_type: str, count: int,
                          video_title: str = "", audio_transcript: str = "",
                          target_chars: int = 0) -> list[dict]:
        """批量生成多个差异化脚本。"""
        scripts = []
        for i in range(count):
            logger.info("生成第 %d/%d 个脚本...", i + 1, count)
            script = self.generate(
                synthesis, script_type=script_type,
                video_title=video_title, audio_transcript=audio_transcript,
                variation_seed=i + 1, target_chars=target_chars,
            )
            scripts.append(script)
            # 节流：避免连续请求打爆 API
            if i < count - 1:
                time.sleep(1.5)
        return scripts

    def _call_api(self, prompt: str, target_chars: int = 0) -> str:
        """调用 AI API，返回原始文本，带限流重试。max_tokens 根据目标字数动态限制。

        max_tokens 公式：中文字符 ≈ 1.2-1.5 token/字，JSON结构≈300 token，留余量×1.3。
        150字参考→535，200字→650，300字→885，500字→1275。
        """
        if target_chars > 0:
            base = int(target_chars * 1.5) + 300
            max_tok = max(400, int(base * 1.3))
        else:
            max_tok = 1500
        logger.info("API 调用 max_tokens=%d (target_chars=%d)", max_tok, target_chars)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=AGNES_MODEL,
                    messages=[
                        {"role": "system",
                         "content": "你是短视频脚本策划。输出纯 JSON，不用 markdown 包裹。"
                                    "内容简洁精炼，篇幅严格匹配参考视频。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tok, temperature=0.3, timeout=120,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                msg = str(e).lower()
                is_rate_limited = any(kw in msg for kw in
                    ("429", "rate limit", "too many requests", "503", "service unavailable"))
                if is_rate_limited and attempt < max_retries - 1:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning("API 限流，等待 %.1fs 后重试 (%d/%d)...", wait, attempt + 1, max_retries)
                    time.sleep(wait)
                    continue
                raise

    def _parse_json(self, raw: str, retry_prompt: str = "") -> dict:
        """解析 AI 返回的 JSON，失败时触发重试."""
        try:
            md = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
            if md:
                raw = md.group(1).strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start:end + 1]
            return json.loads(raw)
        except ValueError as e:
            if retry_prompt:
                logger.warning("JSON 解析失败，重试中... (%s)", e)
                correction = (
                    f"上次输出 JSON 解析失败：{e}。"
                    f"请严格按格式输出纯 JSON，不要用 markdown 包裹，"
                    f"确保所有字符串用双引号，没有尾随逗号。\n\n"
                    f"原始需求：\n{retry_prompt}"
                )
                raw2 = self._call_api(correction)
                return self._parse_json(raw2)
            raise ScriptGeneratorError(f"JSON 解析失败: {e}\n原始输出: {raw[:300]}")

    def detect_type(self, synthesis: str, video_title: str, audio_transcript: str = "") -> str:
        """根据视频内容自动检测脚本类型.

        图文类/知识分享类 → 混剪
        剧情类/对话类/真人出镜 → 口播
        失败时默认返回 "mix"。
        """
        # 从音频转录中提取前800字作为补充材料，帮助判断是否有对话结构
        transcript_excerpt = audio_transcript[:800] if audio_transcript else "（无音频转录）"
        prompt = f"""分析以下视频内容，判断它更适合制作"混剪脚本"还是"口播脚本"。

视频标题: {video_title}

视频综合分析:
{synthesis[:2000]}

音频转录节选:
{transcript_excerpt}

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
                timeout=30,
            )
            answer = response.choices[0].message.content.strip().lower()
            if "oral" in answer or "口播" in answer:
                return "oral"
            return "mix"
        except Exception as e:
            logger.warning(f"脚本类型检测失败，默认使用混剪: {e}")
            return "mix"

    def review(self, script: dict, script_type: str, original_prompt: str,
               synthesis: str = "", audio_transcript: str = "", target_chars: int = 0) -> tuple:
        """AI 自检审核：对照原始 Prompt 逐项校验，修正格式偏差和内容缺失。

        新增：计算脚本与参考视频的内容相似度，超过 40% 时要求 AI 降重改写。
        最多重试 3 次，3 次都不行就用最后一版（不再回退原版）。

        Args:
            script: 已生成的脚本 JSON
            script_type: "mix" 或 "oral"
            original_prompt: 原始生成 Prompt
            synthesis: 视频综合分析文本（用于相似度计算）
            audio_transcript: 音频转录文本（用于相似度计算）
            target_chars: 目标字数（用于长度校验）

        Returns:
            (script, note): 修正后的脚本 + 审核结果描述
        """
        # 计算相似度
        similarity = self._compute_similarity(script, script_type, synthesis, audio_transcript)
        ref_word_count = target_chars if target_chars > 0 else 0
        logger.info("相似度: %.1f%%, 参考字数: %d", similarity * 100, ref_word_count)

        prompt = build_review_prompt(script, script_type, original_prompt,
                                     similarity=similarity, ref_word_count=ref_word_count)
        type_label = "口播" if script_type == "oral" else "混剪"
        logger.info("审核微调%s脚本...", type_label)

        last_script = script
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                raw = self._call_api(prompt)
                refined = self._parse_json(raw, retry_prompt=prompt)
                last_script = refined  # 解析成功，记录为最新可用版本
                self._validate(refined, script_type)
                note = "审核通过" if attempt == 0 else f"审核通过（第 {attempt+1} 次）"
                logger.info("审核微调完成（第 %d 次）", attempt + 1)
                return refined, note
            except (ScriptGeneratorError, json.JSONDecodeError) as e:
                if attempt < max_retries:
                    logger.warning("审核校验失败（第 %d/%d 次）: %s，重试中...", attempt + 1, max_retries + 1, e)
                    prompt = (
                        f"⚠️ 上一次修正被拒绝，原因：{e}\n"
                        f"请修正上述问题，重新输出完整的纯 JSON。\n\n"
                        + prompt
                    )

        # 所有重试耗尽：用最后一版，尝试程序化补标记
        logger.warning("审核微调 %d 次均未完全通过校验，使用最后一版", max_retries + 1)

        # 兜底：不依赖 AI，程序化修复缺失的【标记】
        if script_type == "oral":
            last_script = self._fix_markers(last_script)
            try:
                self._validate(last_script, script_type)
                logger.info("程序化修复后校验通过")
                return last_script, "审核通过（自动修复）"
            except ScriptGeneratorError as e:
                logger.warning("程序化修复后仍不通过: %s", e)

        return last_script, "审核未完全通过，使用最后一版（请人工复核）"

    def _find_missing_markers(self, script: dict) -> list:
        """扫描口播脚本，返回所有缺少末尾【标记】的对话轮号列表。"""
        missing = []
        for i, d in enumerate(script.get("dialogs", [])):
            if isinstance(d, list) and len(d) >= 2:
                text = str(d[1]).strip() if d[1] else ""
                if not re.search(r'【[^】]+】\s*$', text):
                    missing.append(i)
        return missing

    def _fix_markers(self, script: dict) -> dict:
        """程序化补全口播脚本中缺失的末尾【标记】。不依赖 AI，稳定可靠。"""
        if "dialogs" not in script:
            return script
        default_markers = [
            "真诚分享", "好奇追问", "恍然大悟", "热心推荐", "感慨万千",
            "积极鼓励", "认真分析", "由衷赞叹", "无奈摇头", "充满期待",
        ]
        fixed_count = 0
        for i, d in enumerate(script["dialogs"]):
            if isinstance(d, list) and len(d) >= 2:
                text = str(d[1]).strip() if d[1] else ""
                if not re.search(r'【[^】]+】\s*$', text):
                    marker = default_markers[i % len(default_markers)]
                    d[1] = text + f"【{marker}】"
                    fixed_count += 1
        if fixed_count > 0:
            logger.info("程序化补全了 %d 处缺失的【标记】", fixed_count)
        return script

    def _compute_similarity(self, script: dict, script_type: str,
                            synthesis: str = "", audio_transcript: str = "") -> float:
        """计算生成脚本与参考视频内容之间的字符三元组 Jaccard 相似度。

        返回值范围 0-1。返回 0 表示无法计算（参考文本为空）。
        阈值：超过 0.4（40%）视为相似度过高，需要降重。
        """
        # 提取参考文本
        ref_parts = []
        if audio_transcript:
            ref_parts.append(audio_transcript)
        if synthesis:
            ref_parts.append(synthesis)
        ref_text = " ".join(ref_parts)
        if not ref_text.strip():
            return 0.0

        # 提取脚本中所有文本内容
        script_parts = []
        if script_type == "oral":
            script_parts.append(script.get("original_text", ""))
            for d in script.get("dialogs", []):
                if isinstance(d, list) and len(d) >= 2:
                    script_parts.append(str(d[1]))
        else:
            for r in script.get("rows", []):
                if isinstance(r, list) and len(r) >= 1:
                    script_parts.append(str(r[0]))
        script_text = " ".join(script_parts)
        if not script_text.strip():
            return 0.0

        # 字符三元组提取（适合中文，无需分词）
        def char_ngrams(text: str, n: int = 3) -> set:
            # 只保留中文字符和字母数字
            cleaned = re.sub(r'[^一-鿿\w]', '', text)
            if len(cleaned) < n:
                return {cleaned}
            return {cleaned[i:i + n] for i in range(len(cleaned) - n + 1)}

        script_ngrams = char_ngrams(script_text)
        ref_ngrams = char_ngrams(ref_text)

        if not script_ngrams or not ref_ngrams:
            return 0.0

        intersection = script_ngrams & ref_ngrams
        union = script_ngrams | ref_ngrams
        return len(intersection) / len(union) if union else 0.0

    def _validate(self, script: dict, script_type: str = "mix"):
        """验证脚本结构，校验阈值对齐 requirements.json。长度由 max_tokens 控制，不在此校验。"""
        req = load_requirements()

        if "title" not in script:
            raise ScriptGeneratorError("缺少 title")
        if "title" in script and not script.get("title", "").strip():
            raise ScriptGeneratorError("title 为空")
        if "hashtags" not in script or not isinstance(script.get("hashtags"), list):
            raise ScriptGeneratorError("缺少 hashtags 数组")

        # 清理 + 校验话题词数量（交付要求 4-5 个）
        hashtags = [t for t in script["hashtags"] if isinstance(t, str) and t.strip()]
        if len(hashtags) < 4:
            raise ScriptGeneratorError(
                f"话题词不足: {len(hashtags)} 个（至少需要 4 个）。"
                f"请确保生成的话题词为中文短语，如：职场干货、面试技巧、求职")
        # 话题词中不允许出现品牌名
        brand = req.get("混剪", {}).get("广告", {}).get("品牌", "鱼泡直聘")
        for t in hashtags:
            if brand in t:
                raise ScriptGeneratorError(
                    f"话题词「{t}」包含品牌名「{brand}」。"
                    f"话题词是内容标签，严禁出现品牌名。请替换为通用的内容主题词。")
        script["hashtags"] = hashtags

        if script_type == "oral":
            if "original_text" not in script:
                raise ScriptGeneratorError("缺少 original_text")
            if "dialogs" not in script or not isinstance(script["dialogs"], list):
                raise ScriptGeneratorError("缺少 dialogs 数组")

            dia_range = req.get("口播", {}).get("对话轮数范围", [8, 20])
            dia_lo, dia_hi = dia_range[0], dia_range[1]
            actual_count = len(script["dialogs"])
            if actual_count < dia_lo or actual_count > dia_hi:
                raise ScriptGeneratorError(
                    f"对话轮数不符: 需要 {dia_lo}-{dia_hi} 轮，实际 {actual_count} 轮")

            for i, d in enumerate(script["dialogs"]):
                if not isinstance(d, list) or len(d) < 2:
                    raise ScriptGeneratorError(f"第{i}轮对话格式错误（需要[角色,对话]）")
                role, text = d[0], d[1]
                # 角色名只能是纯字母 A 或 B
                if str(role).strip() not in ("A", "B"):
                    raise ScriptGeneratorError(
                        f"第{i}轮角色名错误: '{role}'（只能是 'A' 或 'B'）")
                # 对话内容末尾必须有【标记】（2-4字）
                text_str = str(text).strip() if text else ""
                if not text_str:
                    raise ScriptGeneratorError(f"第{i}轮对话内容为空")
                # 检查末尾【标记】
                marker_match = re.search(r'【[^】]+】\s*$', text_str)
                if not marker_match:
                    raise ScriptGeneratorError(
                        f"第{i}轮对话缺少末尾【情绪/动作标记】，"
                        f"请在对话末尾添加如【恍然大悟】【好奇追问】等标记")
                # 标记长度检查
                marker_text = marker_match.group().strip()
                inner = re.sub(r'[【】]', '', marker_text)
                if len(inner) < 1:
                    raise ScriptGeneratorError(
                        f"第{i}轮对话末尾标记为空，请填写2-4字的情绪/动作描述")

            if "images" not in script or not isinstance(script.get("images"), list):
                raise ScriptGeneratorError("缺少 images 数组")
        else:
            if "rows" not in script or not isinstance(script["rows"], list):
                raise ScriptGeneratorError("缺少 rows 数组")
            lo = req.get("混剪", {}).get("行数范围", [10, 16])[0]
            min_rows = max(3, min(5, lo))
            if len(script["rows"]) < min_rows:
                raise ScriptGeneratorError(
                    f"行数不足: {len(script['rows'])} (至少{min_rows}行)")
            for i, row in enumerate(script["rows"]):
                if not isinstance(row, list) or len(row) < 2:
                    raise ScriptGeneratorError(f"第{i}行格式错误（需要[内容,素材]）")
