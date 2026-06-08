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

        # 一次 AI 调用，直接解析 JSON（不重试，失败就报错）
        raw = self._call_api(prompt, target_chars=target_chars,
                            script_type=script_type)
        script = self._parse_json(raw)

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

    def _call_api(self, prompt: str, target_chars: int = 0,
                  script_type: str = "mix") -> str:
        """调用 AI API，返回原始文本，带限流重试。"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=AGNES_MODEL,
                    messages=[
                        {"role": "system",
                         "content": "你是短视频脚本策划。输出纯 JSON，不用 markdown 包裹。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3, timeout=120,
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

    def detect_type(self, audio_transcript: str = "") -> str:
        """根据音频转录检测脚本类型。

        只看音频转录文字本身：是否有明显的多角色对话模式（换人说话、问答等）。
        失败时默认返回 "mix"。
        """
        if not audio_transcript:
            return "mix"

        transcript_excerpt = audio_transcript[:1200]
        prompt = f"""判断以下音频转录文字中，是单个人在讲还是两个多个人在对话。

音频转录：
{transcript_excerpt}

- 如果从头到尾一个人讲 → 回复 mix
- 如果能听出两个或多个人在交替对话 → 回复 oral

只回复一个词: mix 或 oral。"""

        try:
            response = self._client.chat.completions.create(
                model=AGNES_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.1,
                timeout=30,
            )
            answer = response.choices[0].message.content.strip().lower()
            if "oral" in answer:
                return "oral"
            return "mix"
        except Exception as e:
            logger.warning(f"脚本类型检测失败，默认使用混剪: {e}")
            return "mix"

    def review(self, script: dict, script_type: str,
               synthesis: str = "", target_chars: int = 0) -> tuple:
        """AI 二次仿写审核：诊断长度偏差和相似度，聚焦缩写/扩写/降重改写。

        重试最多 1 次，失败回退原版。

        Args:
            script: 已生成的脚本 JSON
            script_type: "mix" 或 "oral"
            synthesis: 视频综合分析文本
            target_chars: 参考视频口播字数

        Returns:
            (script, note): 修正后的脚本 + 审核结果描述
        """
        # 计算脚本内容字数和相似度
        script_chars = self._count_script_chars(script, script_type)
        similarity = self._compute_similarity(script, script_type, synthesis)
        logger.info("审核诊断: script_chars=%d, target_chars=%d, similarity=%.1f%%",
                    script_chars, target_chars, similarity * 100)

        prompt = build_review_prompt(script,
                                     synthesis=synthesis,
                                     target_chars=target_chars,
                                     similarity=similarity,
                                     script_chars=script_chars)
        type_label = "口播" if script_type == "oral" else "混剪"
        logger.info("审核微调%s脚本...", type_label)

        # 最多 2 次尝试：首次 + 1 次重试
        for attempt in range(2):
            try:
                raw = self._call_api(prompt, script_type=script_type)
                refined = self._parse_json(raw, retry_prompt=prompt)
                self._validate(refined, script_type)

                # 二次审核后的相似度复查
                new_chars = self._count_script_chars(refined, script_type)
                new_sim = self._compute_similarity(refined, script_type, synthesis)
                logger.info("审核后: chars=%d, similarity=%.1f%%", new_chars, new_sim * 100)

                note = "审核通过" if attempt == 0 else "审核通过（第 2 次）"
                logger.info("审核完成（第 %d 次）", attempt + 1)
                return refined, note
            except (ScriptGeneratorError, json.JSONDecodeError) as e:
                if attempt == 0:
                    logger.warning("审核失败（第 1 次）: %s，重试...", e)
                    # 重试用同一个 prompt，不堆积错误信息
                    prompt = f"上次输出被拒绝：{e}\n请严格按格式输出完整纯 JSON。\n\n" + prompt
                else:
                    logger.warning("审核 2 次均失败，回退原版: %s", e)

        # 耗尽重试：回退原版 + 程序化补标记
        logger.warning("审核回退使用原始脚本")
        if script_type == "oral":
            script = self._fix_markers(dict(script))  # 副本上修复
        return script, "审核未通过，使用原始脚本（请人工复核）"

    def _count_script_chars(self, script: dict, script_type: str) -> int:
        """统计「正式创作内容」的中文字数。口播只统计 dialogs，original_text 是原文还原，长度约束不包含它。"""
        import re as _re
        parts = []
        if script_type == "oral":
            for d in script.get("dialogs", []):
                if isinstance(d, list) and len(d) >= 2:
                    parts.append(str(d[1]))
            for i in script.get("images", []):
                parts.append(str(i))
        else:
            for r in script.get("rows", []):
                if isinstance(r, list) and len(r) >= 1:
                    parts.append(str(r[0]))
                if isinstance(r, list) and len(r) >= 2:
                    parts.append(str(r[1]))
        # 统计中文字符
        text = " ".join(parts)
        return len(_re.findall(r'[一-鿿]', text))

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
                            synthesis: str = "") -> float:
        """计算生成脚本与参考视频内容之间的字符三元组 Jaccard 相似度。

        返回值范围 0-1。返回 0 表示无法计算（参考文本为空）。
        阈值：超过 0.4（40%）视为相似度过高，需要降重。
        """
        # 参考文本只用 synthesis（视频分析），不用 audio_transcript（原文转录）
        ref_text = (synthesis or "")
        if not ref_text.strip():
            return 0.0

        # 提取「创作内容」文本，口播不比较 original_text（它是原文还原，天然高度相似）
        script_parts = []
        if script_type == "oral":
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
                # 对话内容末尾必须有【标记】
                text_str = str(text).strip() if text else ""
                if not text_str:
                    raise ScriptGeneratorError(f"第{i}轮对话内容为空")
                # 检查末尾【标记】
                marker_match = re.search(r'【[^】]+】\s*$', text_str)
                if not marker_match:
                    raise ScriptGeneratorError(
                        f"第{i}轮对话缺少末尾【情绪/动作标记】，"
                        f"请在对话末尾添加如【恍然大悟】【好奇追问】等标记")

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
