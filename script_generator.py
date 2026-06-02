"""
AI 脚本生成：根据视频分析结果生成混剪/口播脚本。
"""
import json
import logging
import re

from openai import OpenAI

from config import (
    AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL,
    MIX_SCRIPT_PROMPT, ORAL_SCRIPT_PROMPT,
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
                 script_type: str = "mix") -> dict:
        """生成脚本.

        Args:
            synthesis: AI 视频综合分析文本
            video_title: 原视频标题
            script_type: "mix" 或 "oral"

        Returns:
            parsed script dict
        """
        if script_type == "mix":
            prompt = MIX_SCRIPT_PROMPT.format(synthesis=synthesis)
        else:
            prompt = ORAL_SCRIPT_PROMPT.format(synthesis=synthesis)

        logger.info(f"生成{script_type}脚本...")

        # 尝试让 AI 返回 JSON
        for attempt in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=AGNES_MODEL,
                    messages=[
                        {"role": "system", "content": "你是一个专业的短视频脚本策划专家。请严格按JSON格式输出，不要用markdown代码块包裹。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4000,
                    temperature=0.8,
                )

                raw_text = response.choices[0].message.content.strip()
                script = self._parse_json(raw_text)

                # 基本验证
                self._validate_script(script, script_type)

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
    # 验证
    # ============================================================

    @staticmethod
    def _validate_script(script: dict, script_type: str):
        """验证脚本结构."""
        if script_type == "mix":
            if "title" not in script:
                raise ScriptGeneratorError("混剪脚本缺少 title 字段")
            if "rows" not in script or not isinstance(script["rows"], list):
                raise ScriptGeneratorError("混剪脚本缺少 rows 数组")
            if len(script["rows"]) < 5:
                raise ScriptGeneratorError(f"混剪脚本行数太少: {len(script['rows'])}")
            for i, row in enumerate(script["rows"]):
                if not isinstance(row, list) or len(row) < 2:
                    raise ScriptGeneratorError(f"第{i}行格式错误（需要[内容, 素材]）")

        else:  # oral
            if "title" not in script:
                raise ScriptGeneratorError("口播脚本缺少 title 字段")
            if "original_text" not in script:
                raise ScriptGeneratorError("口播脚本缺少 original_text 字段")
            if "dialogs" not in script or not isinstance(script["dialogs"], list):
                raise ScriptGeneratorError("口播脚本缺少 dialogs 数组")
            if "images" not in script or not isinstance(script["images"], list):
                raise ScriptGeneratorError("口播脚本缺少 images 数组")
            if len(script["dialogs"]) < 8:
                raise ScriptGeneratorError(f"口播脚本对话轮数太少: {len(script['dialogs'])}")
            for i, d in enumerate(script["dialogs"]):
                if not isinstance(d, list) or len(d) < 3:
                    raise ScriptGeneratorError(f"第{i}轮对话格式错误（需要[角色, 对话, 情绪]）")

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
