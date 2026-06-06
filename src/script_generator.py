"""AI 脚本生成：根据视频分析结果生成混剪/口播脚本。"""
import json
import logging
import re
from openai import OpenAI
from config import AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL, load_requirements
from src.prompt_builder import build_mix_prompt, build_oral_prompt

logger = logging.getLogger(__name__)


class ScriptGeneratorError(Exception):
    pass


class ScriptGenerator:

    def __init__(self):
        self._client = OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY,
                             timeout=120.0)

    def generate(self, synthesis: str, script_type: str = "mix",
                 video_title: str = "", audio_transcript: str = "") -> dict:
        """生成脚本。"""
        if script_type == "oral":
            prompt = build_oral_prompt(synthesis, audio_transcript=audio_transcript)
        else:
            prompt = build_mix_prompt(synthesis, audio_transcript=audio_transcript)

        type_label = "口播" if script_type == "oral" else "混剪"
        src = f"（来源: {video_title}）" if video_title else ""
        logger.info("生成%s脚本%s...", type_label, src)

        max_retries = 2
        last_error = None
        for attempt in range(max_retries + 1):
            raw = self._call_api(prompt)
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

    def _call_api(self, prompt: str) -> str:
        """调用 AI API，返回原始文本."""
        response = self._client.chat.completions.create(
            model=AGNES_MODEL,
            messages=[
                {"role": "system",
                 "content": "你是短视频脚本策划。输出纯 JSON，不用 markdown 包裹。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4000, temperature=0.3, timeout=120,
        )
        return response.choices[0].message.content.strip()

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

    def _validate(self, script: dict, script_type: str = "mix"):
        """验证脚本结构，校验阈值对齐 requirements.json."""
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
