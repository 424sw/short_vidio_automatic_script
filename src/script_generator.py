"""AI 脚本生成：根据视频分析结果生成混剪/口播脚本。"""
import json
import logging
import re
import time
from openai import OpenAI
from config import AGNES_BASE_URL, AGNES_API_KEY, AGNES_MODEL, load_requirements, \
    AI_TIMEOUT_GENERATE
from src.prompt_builder import build_mix_prompt, build_oral_prompt

logger = logging.getLogger(__name__)


class ScriptGeneratorError(Exception):
    pass


class ScriptGenerator:

    def __init__(self):
        self._client = OpenAI(base_url=AGNES_BASE_URL, api_key=AGNES_API_KEY,
                             timeout=float(AI_TIMEOUT_GENERATE))

    def generate(self, script_type: str = "mix",
                 video_title: str = "", audio_transcript: str = "",
                 target_lo: int = 0, target_chars: int = 0) -> dict:
        """生成脚本。一次 AI 调用 + 最多一次 JSON 格式修复，不做内容校验。"""
        if script_type == "oral":
            prompt = build_oral_prompt(audio_transcript=audio_transcript,
                                       target_lo=target_lo, target_chars=target_chars)
        else:
            prompt = build_mix_prompt(audio_transcript=audio_transcript,
                                      target_lo=target_lo, target_chars=target_chars)

        type_label = "口播" if script_type == "oral" else "混剪"
        src = f"（来源: {video_title}）" if video_title else ""
        logger.info("生成%s脚本%s...", type_label, src)

        raw = self._call_api(prompt, target_chars=target_chars,
                            script_type=script_type)
        script = self._parse_json(raw)

        logger.info("%s脚本生成成功（由审核步骤负责质量检查）", type_label)
        return script

    def _call_api(self, prompt: str, target_chars: int = 0,
                  script_type: str = "mix") -> str:
        """调用 AI API，返回原始文本，任意异常均重试（最多3次）。"""
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
                    temperature=0.3, timeout=AI_TIMEOUT_GENERATE,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning("API 调用失败 (attempt %d/%d)，%.1fs 后重试: %s",
                                   attempt + 1, max_retries, wait, e)
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

    def review(self, script: dict, script_type: str,
               audio_transcript: str = "", target_lo: int = 0, target_chars: int = 0) -> dict:
        """全维度程序化合规检查。不调用 AI，纯 Python 判定。"""
        report = {
            "format": {"pass": True, "issues": []},
            "length": {"pass": True, "chars": 0, "target_lo": target_lo, "target_hi": target_chars},
            "similarity": {"pass": True, "score": 0.0},
            "ai_flavor": {"pass": True, "detected": []},
            "marker_distribution": {"pass": True, "details": "N/A"},
            "paragraph_count": {"pass": True, "issues": []},
            "ad_placement": {"pass": True, "issues": []},
            "punctuation": {"pass": True, "issues": []},
        }

        type_label = "口播" if script_type == "oral" else "混剪"

        # 1. 格式检查
        try:
            self._validate(script, script_type)
        except ScriptGeneratorError as e:
            report["format"]["pass"] = False
            report["format"]["issues"].append(str(e))

        # 1.5 混剪段数检查
        if script_type == "mix":
            report["paragraph_count"] = self._check_paragraph_count(script)
            report["punctuation"] = self._check_punctuation(script)

        # 2. 长度检查
        script_chars = self._count_script_chars(script, script_type)
        report["length"]["chars"] = script_chars
        if target_lo > 0 and target_chars > 0:
            if script_chars < target_lo or script_chars > target_chars:
                report["length"]["pass"] = False

        # 3. 相似度检查
        similarity = self._compute_similarity(script, script_type, audio_transcript)
        report["similarity"]["score"] = similarity
        if similarity > 0.4:
            report["similarity"]["pass"] = False

        # 4. 情绪标记分布（仅口播）
        if script_type == "oral":
            report["marker_distribution"] = self._check_marker_distribution(script)

        # 5. AI 味检测
        report["ai_flavor"] = self._check_ai_flavor(script, script_type)

        # 6. 广告植入检查
        report["ad_placement"] = self._check_brand_presence(script, script_type)

        report["needs_rollback"] = not report["format"]["pass"] or not report["length"]["pass"]
        report["needs_micro"] = (
            not report["similarity"]["pass"]
            or not report["ai_flavor"]["pass"]
            or not report["marker_distribution"]["pass"]
            or not report["paragraph_count"]["pass"]
            or not report["ad_placement"]["pass"]
            or not report["punctuation"]["pass"]
        )

        logger.info("审核 %s: format=%s length=%s chars=%d/%d~%d sim=%.0f%% ai_flavor=%s marker=%s para=%s ad=%s punct=%s → rollback=%s micro=%s",
                    type_label,
                    "✓" if report["format"]["pass"] else "✗",
                    "✓" if report["length"]["pass"] else "✗",
                    script_chars, target_lo, target_chars,
                    similarity * 100,
                    "✓" if report["ai_flavor"]["pass"] else "✗",
                    "✓" if report["marker_distribution"]["pass"] else "✗",
                    "✓" if report["paragraph_count"]["pass"] else "✗",
                    "✓" if report["ad_placement"]["pass"] else "✗",
                    "✓" if report["punctuation"]["pass"] else "✗",
                    "Y" if report["needs_rollback"] else "N",
                    "Y" if report["needs_micro"] else "N")

        return report

    def _count_script_chars(self, script: dict, script_type: str) -> int:
        """统计「正式创作内容」的中文字数。口播只统计 dialogs，original_text 是原文还原，长度约束不包含它。"""
        import re as _re
        parts = []
        if script_type == "oral":
            for d in script.get("dialogs", []):
                if isinstance(d, list) and len(d) >= 2:
                    parts.append(str(d[1]))
        else:
            for r in script.get("rows", []):
                if isinstance(r, list) and len(r) >= 1:
                    parts.append(str(r[0]))
        text = " ".join(parts)
        return len(_re.findall(r'[一-鿿]', text))

    def _check_paragraph_count(self, script: dict) -> dict:
        """混剪：每行分段数检查。

        规则：
        - 5 句及以上 → 不合格
        - 2/3 句行数量尽量平衡（少数方 ≥ 25%），2+3 句行合计 ≥ 65%
        """
        issues = []
        counts = {1: 0, 2: 0, 3: 0, 4: 0, "5+": 0}
        total = 0
        for i, row in enumerate(script.get("rows", [])):
            if isinstance(row, list) and len(row) >= 1:
                paragraphs = [p for p in str(row[0]).split("\n") if p.strip()]
                n = len(paragraphs)
                total += 1
                if n >= 5:
                    counts["5+"] += 1
                    issues.append(f"第{i+1}行 {n} 段 → 需合并到 2-4 段")
                elif n == 0:
                    counts[1] += 1
                elif n in counts:
                    counts[n] += 1

        if total == 0:
            return {"pass": True, "issues": []}

        # ① 5+ segments → fail
        if counts["5+"] > 0:
            return {"pass": False, "issues": issues}

        n2, n3 = counts[2], counts[3]
        n23 = n2 + n3

        # ② 2+3 < 65% → fail
        if n23 / total < 0.65:
            issues.append(
                f"2-3句行占比不足: {n23}/{total} ({n23*100//total}%，需≥65%)")
            return {"pass": False, "issues": issues}

        # ③ 2 vs 3 失衡 → fail
        if n23 > 0:
            minority_ratio = min(n2, n3) / n23
            if minority_ratio < 0.25:
                issues.append(
                    f"2句行({n2})与3句行({n3})数量失衡 (少数方{minority_ratio:.0%})")
                return {"pass": False, "issues": issues}

        return {"pass": True, "issues": []}

    def _check_brand_presence(self, script: dict, script_type: str) -> dict:
        """检查广告品牌是否在脚本正文中出现。

        扫描混剪 rows[0] 或口播 dialogs[1]，搜索配置中的品牌名。
        """
        from config import load_requirements
        req = load_requirements()
        brand = req.get("混剪", {}).get("广告", {}).get("品牌", "鱼泡直聘")

        texts = []
        if script_type == "oral":
            for d in script.get("dialogs", []):
                if isinstance(d, list) and len(d) >= 2:
                    texts.append(str(d[1]))
        else:
            for r in script.get("rows", []):
                if isinstance(r, list) and len(r) >= 1:
                    texts.append(str(r[0]))
        combined = " ".join(texts)

        if brand not in combined:
            return {"pass": False, "issues": [f"广告品牌「{brand}」未在脚本正文中出现"]}
        return {"pass": True, "issues": []}

    def _check_punctuation(self, script: dict) -> dict:
        """混剪：检查正文中是否包含标点符号。

        规则：文案必须纯口语，用自然换行分隔停顿，不允许任何标点符号。
        """
        # 中文标点 + 英文标点（允许空格和换行作为分隔）
        punct_pattern = re.compile(
            r'[]，。！？、；：""''「」『』【】《》（）…—～,.!?;:\"\'#@&*+=/\\|<>`~^[({}-]'
        )
        issues = []
        for i, row in enumerate(script.get("rows", [])):
            if isinstance(row, list) and len(row) >= 1:
                text = str(row[0])
                matches = punct_pattern.findall(text)
                if matches:
                    unique = list(dict.fromkeys(matches))  # 去重保序
                    issues.append(f"第{i+1}行含标点: {''.join(unique)}")
        if issues:
            return {"pass": False, "issues": issues}
        return {"pass": True, "issues": []}

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
        """程序化补全口播脚本中缺失的末尾【标记】。"""
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

    def _check_marker_distribution(self, script: dict) -> dict:
        """检查口播脚本情绪标记：只允许 2/4 字 + 数量基本对称。
        返回 pass=False 当且仅当满足以下任一条件：
        - 存在 3/5/其他长度（非法）
        - 2 字和 4 字数量严重失衡（少数方 < 30%）
        """
        markers = []
        for d in script.get("dialogs", []):
            if isinstance(d, list) and len(d) >= 2:
                text = str(d[1]).strip() if d[1] else ""
                m = re.search(r'【([^】]+)】\s*$', text)
                if m:
                    markers.append(m.group(1))

        if not markers:
            return {"pass": True, "details": "无标记"}

        total = len(markers)
        len_2 = sum(1 for m in markers if len(m) == 2)
        len_4 = sum(1 for m in markers if len(m) == 4)
        len_other = total - len_2 - len_4
        details = f"{total}个标记: 2字×{len_2}, 4字×{len_4}"

        if len_other > 0:
            illegal = [m for m in markers if len(m) not in (2, 4)]
            return {"pass": False, "details": f"非法长度: {illegal} → {details}"}

        minority_ratio = min(len_2, len_4) / total if total > 0 else 0
        if minority_ratio < 0.25:
            return {"pass": False, "details": f"失衡: {details}"}

        return {"pass": True, "details": details}

    def micro_adjust(self, script: dict, script_type: str, report: dict,
                     audio_transcript: str = "", target_lo: int = 0, target_chars: int = 0) -> dict:
        """AI 微调正文：一次 AI 调用修复格式/长度/相似度/AI 味/段数。"""
        script_text = json.dumps(script, ensure_ascii=False, indent=2)
        diagnoses = []
        instructions = []

        if not report["format"]["pass"]:
            for issue in report["format"]["issues"]:
                diagnoses.append(f"格式错误: {issue}")
            instructions.append("修复上述格式问题，确保 JSON 所有字段和结构符合要求")

        if not report.get("paragraph_count", {}).get("pass", True):
            for issue in report["paragraph_count"]["issues"]:
                diagnoses.append(f"段落节奏: {issue}")
            instructions.append(
                "段落节奏修复：① 5句以上的行，把行内某些断句合并，减至2-3句；"
                "② 如果3句行太多2句行太少，把部分3句行内部的断句合并成2句；"
                "③ 行数不变、每行对应哪个素材不变，只调每行内部的断句数量。不添加标点符号、不改变口播风格")

        if not report["length"]["pass"]:
            chars = report["length"]["chars"]
            lo, hi = target_lo, target_chars
            if chars < lo:
                diagnoses.append(f"字数不足: {chars} 字（需 {lo}~{hi}）")
                instructions.append(f"扩写到 {lo}~{hi} 字，适度补充细节但不要注水")
            else:
                diagnoses.append(f"字数超标: {chars} 字（需 {lo}~{hi}）")
                instructions.append(f"压缩到 {lo}~{hi} 字，删冗余合并同类表达")

        if not report["similarity"]["pass"]:
            sim_pct = report["similarity"]["score"] * 100
            diagnoses.append(f"与参考内容相似度过高: {sim_pct:.0f}%（上限 40%）")
            instructions.append("降重: ①换句式（陈述→反问/感叹）②换案例/举例 ③换措辞（同义替换，不能只改几个词敷衍）")

        if not report["ai_flavor"]["pass"]:
            for item in report["ai_flavor"]["detected"]:
                diagnoses.append(f"AI 写作痕迹: {item}")
            instructions.append("替换或重写上述 AI 痕迹，用口语/接地气的表达替代")

        if not report["ad_placement"]["pass"]:
            for issue in report["ad_placement"]["issues"]:
                diagnoses.append(f"广告缺失: {issue}")
            instructions.append("在脚本前半段自然插入品牌名「鱼泡直聘」及相关产品介绍（1-2句话），不要生硬打断原有节奏")

        if not report["punctuation"]["pass"]:
            for issue in report["punctuation"]["issues"]:
                diagnoses.append(f"标点符号: {issue}")
            instructions.append("移除所有标点符号（逗号、句号、感叹号、问号等），用自然换行分隔停顿，保持纯口语节奏")

        if not diagnoses:
            return script

        diagnosis_text = "\n".join(f"- {d}" for d in diagnoses)
        instruction_text = "\n".join(f"{i+1}. {instr}" for i, instr in enumerate(instructions))

        is_oral = script_type == "oral"
        marker_rule = "-\n 🔴 每轮对话末尾的【标记】一个都不许改\n" if is_oral else ""

        prompt = f"""你是短视频脚本编辑。下面脚本有若干问题需要修复。

## 诊断
{diagnosis_text}

## 修复指令
{instruction_text}

## 约束
- 保持 JSON 结构（字段、类型）完全不变{marker_rule}
- 🔴 original_text 一个字都不许改
- 保持仿写风格和话题方向不变

## 当前脚本
```json
{script_text}
```

输出纯 JSON，无 markdown 包裹。"""

        type_label = "口播" if is_oral else "混剪"
        for attempt in range(2):
            try:
                raw = self._call_api(prompt)
                fixed = self._parse_json(raw, retry_prompt=prompt)
                logger.info("正文微调完成 (%s, attempt %d)", type_label, attempt + 1)
                return fixed
            except Exception as e:
                logger.warning("正文微调失败 (attempt %d): %s", attempt + 1, e)
                if attempt == 0:
                    prompt = f"上次输出 JSON 解析失败：{e}\n请严格按格式输出。\n\n{prompt}"

        logger.warning("正文微调耗尽，回退原版")
        return script

    def micro_adjust_markers(self, script: dict) -> dict:
        """AI 微调口播情绪标记：非法长度→就近合法 + 多数→少数达数量对称。"""
        import random as _random

        dialogs = script.get("dialogs", [])
        if not dialogs:
            return script

        markers_info = []
        for i, d in enumerate(dialogs):
            if isinstance(d, list) and len(d) >= 2:
                text = str(d[1]).strip() if d[1] else ""
                m = re.search(r'【([^】]+)】\s*$', text)
                if m:
                    markers_info.append((i, m.group(1), len(m.group(1))))

        if not markers_info:
            return script

        # Phase 1: fix illegal lengths (not 2 or 4)
        illegal = [(i, mk, sz) for (i, mk, sz) in markers_info if sz not in (2, 4)]
        if illegal:
            to_fix = [info[1] for info in illegal]
            target_lens = [4 if info[2] >= 4 else 2 for info in illegal]
            prompt = f"""修正以下情绪标记的长度。
原始标记: {json.dumps(to_fix, ensure_ascii=False)}
目标长度: {json.dumps(target_lens, ensure_ascii=False)}
规则: 保留原始情感含义，改为目标字数（2或4字）。
返回: {{"converted": {{"原标记": "新标记", ...}}}}，纯 JSON。"""

            mapping = self._batch_convert_markers(prompt)
            for idx, old_marker, _ in illegal:
                new_marker = mapping.get(old_marker)
                if new_marker and new_marker != old_marker:
                    dialogs[idx][1] = dialogs[idx][1].replace(
                        f"【{old_marker}】", f"【{new_marker}】")
            # Refresh markers_info after fixing illegal lengths
            markers_info = []
            for i, d in enumerate(dialogs):
                if isinstance(d, list) and len(d) >= 2:
                    text = str(d[1]).strip() if d[1] else ""
                    m = re.search(r'【([^】]+)】\s*$', text)
                    if m:
                        markers_info.append((i, m.group(1), len(m.group(1))))

        len_2 = [(i, mk) for (i, mk, sz) in markers_info if sz == 2]
        len_4 = [(i, mk) for (i, mk, sz) in markers_info if sz == 4]
        n2, n4 = len(len_2), len(len_4)

        if n2 + n4 < 2:
            return script

        # Phase 2: balance — convert from majority to minority
        target_each = (n2 + n4) // 2
        if n2 < target_each:
            convert_count = min(target_each - n2, n4)
            target_len = 2
        elif n4 < target_each:
            convert_count = min(target_each - n4, n2)
            target_len = 4
        else:
            return script  # already balanced

        if convert_count <= 0:
            return script

        pool = len_4 if target_len == 2 else len_2
        to_convert = _random.sample(pool, convert_count)
        marker_list = [mk for (_, mk) in to_convert]

        logger.info("标记微调: 2字×%d 4字×%d → %d个转%d字以求平衡",
                    n2, n4, convert_count, target_len)

        prompt = f"""将以下情绪标记转换为约{target_len}字版本，保留原始情感含义。
标记列表: {json.dumps(marker_list, ensure_ascii=False)}
返回: {{"converted": {{"原标记": "新标记", ...}}}}，纯 JSON。"""

        mapping = self._batch_convert_markers(prompt)
        for idx, old_marker in to_convert:
            new_marker = mapping.get(old_marker)
            if new_marker and new_marker != old_marker:
                dialogs[idx][1] = dialogs[idx][1].replace(
                    f"【{old_marker}】", f"【{new_marker}】")

        return script

    def _batch_convert_markers(self, prompt: str) -> dict:
        """调用 AI 批量转换标记，含算法兜底."""
        for attempt in range(2):
            try:
                raw = self._call_api(prompt, script_type="oral")
                mapping = self._parse_json(raw).get("converted", {})
                if mapping:
                    return mapping
                logger.warning("标记转换 AI 返回空映射 (attempt %d/2)", attempt + 1)
            except Exception as e:
                logger.warning("标记转换 AI 失败 (attempt %d/2): %s", attempt + 1, e)
                if attempt == 0:
                    prompt = f"上次输出 JSON 解析失败：{e}\n请严格按格式输出。\n\n{prompt}"

        logger.info("标记转换 AI 耗尽，使用算法兜底")
        # Simple algorithmic fallback
        import re as _re, ast as _ast
        marker_list_match = _re.search(r'\[([^\]]+)\]', prompt)
        if marker_list_match:
            try:
                marker_list = _ast.literal_eval(marker_list_match.group(0))
            except Exception:
                marker_list = []
        else:
            marker_list = []
        target_len = 2 if "2字" in prompt else 4
        mapping = {}
        for old_marker in marker_list:
            if target_len == 4:
                mapping[old_marker] = old_marker + old_marker  # e.g. "好奇" → "好奇好奇"
            else:
                mapping[old_marker] = old_marker[:2]  # e.g. "好奇追问" → "好奇"
        return mapping

    def _check_ai_flavor(self, script: dict, script_type: str) -> dict:
        """检测生成脚本中的 AI 写作痕迹。"""
        texts = []
        if script_type == "oral":
            for d in script.get("dialogs", []):
                if isinstance(d, list) and len(d) >= 2:
                    texts.append(str(d[1]))
        else:
            for r in script.get("rows", []):
                if isinstance(r, list) and len(r) >= 1:
                    texts.append(str(r[0]))
        combined = " ".join(texts)

        detected = []

        ai_words = [
            "此外", "值得注意的是", "深入探讨", "至关重要的",
            "展现了", "标志着", "见证了", "堪称", "充分",
        ]
        for w in ai_words:
            if w in combined:
                detected.append(f"AI词汇「{w}」")

        if re.search(r'不仅.{0,10}更是', combined):
            detected.append("否定式排比「不仅是...更是...」")
        if re.search(r'不只是.{0,10}而是', combined):
            detected.append("否定式排比「不只是...而是...」")

        promo = ["令人惊叹", "无与伦比", "必看的", "叹为观止", "绝美的"]
        for w in promo:
            if w in combined:
                detected.append(f"宣传腔「{w}」")

        if detected:
            return {"pass": False, "detected": detected}
        return {"pass": True, "detected": []}

    def _compute_similarity(self, script: dict, script_type: str,
                            audio_transcript: str = "") -> float:
        """计算生成脚本与参考视频内容之间的字符三元组 Jaccard 相似度。"""
        ref_text = (audio_transcript or "")
        if not ref_text.strip():
            return 0.0

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

        def char_ngrams(text: str, n: int = 3) -> set:
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
        """验证脚本结构，校验阈值对齐 requirements.json。"""
        req = load_requirements()

        if "title" not in script:
            raise ScriptGeneratorError("缺少 title")
        if "title" in script and not script.get("title", "").strip():
            raise ScriptGeneratorError("title 为空")
        if "hashtags" not in script or not isinstance(script.get("hashtags"), list):
            raise ScriptGeneratorError("缺少 hashtags 数组")

        hashtags = [t for t in script["hashtags"] if isinstance(t, str) and t.strip()]
        if len(hashtags) < 4:
            raise ScriptGeneratorError(
                f"话题词不足: {len(hashtags)} 个（至少需要 4 个）。"
                f"请确保生成的话题词为中文短语，如：职场干货、面试技巧、求职")
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
                role, text = d[0], d[1]
                if str(role).strip() not in ("A", "B"):
                    raise ScriptGeneratorError(
                        f"第{i}轮角色名错误: '{role}'（只能是 'A' 或 'B'）")
                text_str = str(text).strip() if text else ""
                if not text_str:
                    raise ScriptGeneratorError(f"第{i}轮对话内容为空")
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
