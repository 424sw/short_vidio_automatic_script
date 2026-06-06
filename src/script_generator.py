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
        """生成脚本。variation_seed > 0 时注入多样性指令。"""
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

        max_retries = 2
        last_error = None
        for attempt in range(max_retries + 1):
            raw = self._call_api(prompt, target_chars=target_chars)
            script = self._parse_json(raw, retry_prompt=prompt)
            try:
                self._validate(script, script_type)
                logger.info("%s脚本生成成功（第 %d 次）", type_label, attempt + 1)
                return script
            except ScriptGeneratorError as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning("校验失败（第 %d/%d 次）: %s，重试中...", attempt + 1, max_retries + 1, e)
                    prompt = (
                        f"⚠️ 上一次生成被拒绝，原因：{e}\n"
                        f"请修正上述问题，重新输出完整的纯 JSON。\n\n"
                        + prompt
                    )
        raise last_error

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
        """调用 AI API，返回原始文本，带限流重试。max_tokens 根据目标字数动态限制。"""
        if target_chars > 0:
            max_tok = max(1000, int(target_chars * 2.0) + 600)
        else:
            max_tok = 2000
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

    def detect_type(self, synthesis: str, video_title: str) -> str:
        """根据视频内容自动检测脚本类型.

        图文类/知识分享类 → 混剪
        剧情类/对话类/真人出镜 → 口播
        失败时默认返回 "mix"。
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
                timeout=30,
            )
            answer = response.choices[0].message.content.strip().lower()
            if "oral" in answer or "口播" in answer:
                return "oral"
            return "mix"
        except Exception as e:
            logger.warning(f"脚本类型检测失败，默认使用混剪: {e}")
            return "mix"

    def review(self, script: dict, script_type: str, original_prompt: str) -> dict:
        """AI 自检审核：对照原始 Prompt 逐项校验，修正格式偏差和内容缺失。

        审核失败不阻塞，返回原脚本。
        """
        prompt = build_review_prompt(script, script_type, original_prompt)
        type_label = "口播" if script_type == "oral" else "混剪"
        logger.info("审核微调%s脚本...", type_label)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                raw = self._call_api(prompt)
                refined = self._parse_json(raw, retry_prompt=prompt)
                self._validate(refined, script_type)
                logger.info("审核微调完成（第 %d 次）", attempt + 1)
                return refined
            except (ScriptGeneratorError, json.JSONDecodeError) as e:
                if attempt < max_retries:
                    logger.warning("审核校验失败（第 %d/%d 次）: %s，重试中...", attempt + 1, max_retries + 1, e)
                    prompt = (
                        f"⚠️ 上一次修正被拒绝，原因：{e}\n"
                        f"请修正上述问题，重新输出完整的纯 JSON。\n\n"
                        + prompt
                    )
        # 审核失败不阻塞，返回原脚本
        logger.warning("审核微调未通过校验，使用原脚本: %s", str(e) if 'e' in dir() else "未知错误")
        return script

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
            for i, d in enumerate(script["dialogs"]):
                if not isinstance(d, list) or len(d) < 2:
                    raise ScriptGeneratorError(f"第{i}轮对话格式错误（需要[角色,对话]）")
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
