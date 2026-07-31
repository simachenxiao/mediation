from __future__ import annotations

import json
import re
from typing import Any

from ..core.config import get_settings


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)  # 尽量从模型输出里截出 JSON。
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_INT_UNITS = ["", "拾", "佰", "仟"]
_CN_SECTION_UNITS = ["", "万", "亿", "兆"]


def _four_digit_to_cn(value: int) -> str:
    """把 0-9999 的整数转成金额大写片段。"""
    if value == 0:
        return ""
    result: list[str] = []
    zero_pending = False
    unit_pos = 0
    while value:
        digit = value % 10
        if digit == 0:
            zero_pending = bool(result)
        else:
            if zero_pending:
                result.append("零")
                zero_pending = False
            result.append(f"{_CN_DIGITS[digit]}{_CN_INT_UNITS[unit_pos]}")
        value //= 10
        unit_pos += 1
    return "".join(reversed(result))


def _number_to_rmb_upper(raw_amount: str) -> str:
    """生成公安文书中常见的人民币大写金额。"""
    normalized = raw_amount.replace(",", "").strip()
    integer_text, _, fraction_text = normalized.partition(".")
    integer = int(integer_text or "0")
    if integer == 0:
        integer_cn = "零"
    else:
        sections: list[int] = []
        while integer:
            sections.append(integer % 10000)
            integer //= 10000

        parts: list[str] = []
        zero_pending = False
        for index in range(len(sections) - 1, -1, -1):
            section = sections[index]
            if section == 0:
                zero_pending = bool(parts)
                continue
            if parts and (zero_pending or section < 1000):
                parts.append("零")
            parts.append(f"{_four_digit_to_cn(section)}{_CN_SECTION_UNITS[index]}")
            zero_pending = False
        integer_cn = "".join(parts).replace("零零", "零").rstrip("零")

    fraction = (fraction_text + "00")[:2]
    jiao, fen = int(fraction[0]), int(fraction[1])
    if jiao == 0 and fen == 0:
        fraction_cn = "整"
    elif jiao == 0:
        fraction_cn = f"零{_CN_DIGITS[fen]}分"
    elif fen == 0:
        fraction_cn = f"{_CN_DIGITS[jiao]}角整"
    else:
        fraction_cn = f"{_CN_DIGITS[jiao]}角{_CN_DIGITS[fen]}分"
    return f"{integer_cn}元{fraction_cn}"


def _format_clause_amounts(clause: str) -> str:
    """协议条款中明确出现人民币金额时，补齐小写和大写金额。"""
    if "大写" in clause:
        return clause

    amount_pattern = re.compile(r"(?<![\d.])(?:人民币\s*)?(?:￥\s*)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*元")

    def repl(match: re.Match[str]) -> str:
        amount = match.group(1)
        if match.group(2):
            amount = f"{amount}.{match.group(2)}"
        return f"{match.group(0)}（大写：{_number_to_rmb_upper(amount)}）"

    return amount_pattern.sub(repl, clause)


class LLMService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _call_deepseek(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        import httpx

        if not self.settings.deepseek_api_key:
            raise RuntimeError("DeepSeek API key is missing")

        url = self.settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _ensure_deepseek_available(self) -> None:
        if not self.settings.deepseek_api_key:
            raise RuntimeError("DeepSeek API key is missing")

    @staticmethod
    def _require_json(text: str) -> dict[str, Any]:
        result = _extract_json(text)
        if not result:
            raise RuntimeError("LLM returned invalid JSON")
        return result

    def extract(
        self,
        transcript: str,
        case_context: dict[str, Any],
        current_extraction: dict[str, Any] | None = None,
        current_demand: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_deepseek_available()
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是调解内容提炼助手。只输出严格 JSON，不要解释。"
                    "任务是基于本次/历次对话，只对当前当事人的诉求做增量更新："
                    "1. 已有诉求中非空内容没有被新对话否定时应保留；"
                    "2. 只能使用固定事项：道歉、赔偿金额、履行方式、后续承诺、其他；"
                    "3. 履行方式只指赔偿如何履行，例如一次性支付、当场支付、分期、期限；"
                    "4. 不能归入前四项的内容全部归入其他，不得自行新增事项，也不能把前四项的内容写在其他里；"
                    "5. 不要推断、补写或改写另一方诉求；"
                    "6. 道歉是指是否需要对方道歉或能否向对方道歉；"
                    "7. 赔偿金额是指具体赔偿的金额，不能增加赔礼道歉的内容；"
                    "8. 若事项没提及，必需返回无，不能空着；"
                    "9. 输出字段必须为 facts, claims, concessions, attitude。"
                    "facts、claims、concessions 必须是数组，每项是对象；claims/concessions 每项必须包含 topic 和 content，topic 只能取固定事项；"
                    "attitude 必须是对象；可在 claims/concessions 中加入 consistency 字段。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "caseContext": case_context,
                        "transcript": transcript,
                        "currentExtraction": current_extraction or {},
                        "currentDemand": current_demand or {},
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        text = self._call_deepseek(prompt)
        return self._require_json(text)

    def analyze(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
        case_context: dict[str, Any],
        demand_a: dict[str, Any] | None = None,
        demand_b: dict[str, Any] | None = None,
        demand_rows: list[list[Any]] | None = None,
    ) -> dict[str, Any]:
        self._ensure_deepseek_available()
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是调解事项一致性分析助手。只输出严格 JSON，不要解释。"
                    "只根据固定事项判断双方诉求是否一致，不要补写或改写任一方诉求内容。"
                    "固定事项只能是：道歉、赔偿金额、履行方式、后续承诺、其他。"
                    "道歉的一致性是判断违法行为人和受害人之间对于道歉是否达致一致"
                    "赔偿金额一致性的是判断违法行为人和受害人之间对于赔偿金额否达致一致"
                    "履行方式一致性的是判断违法行为人和受害人之间对于赔偿如何履行，例如一次性支付、当场支付、分期、期限否达致一致"
                    "道歉的一致性是判断违法行为人和受害人之间对于道歉是否达致一致"
                    "其他的一致性是判断违法行为人和受害人之间对于其他内容是否达致一致，都是无也是一致"
                    "其中一致性不是指事融内容在字符上的完全一样，是对于事项内容意思及认同一致"
                    "对比输出的结果,如果事项有空，则是待采集、如果不为有空,一致就返回一致，不一致就返回待商榷"
                    "输出字段：commonGrounds, disputePoints, feasibility。"
                    "commonGrounds/disputePoints 必须是数组，每项包含 topic，topic 必须取固定事项。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "caseContext": case_context,
                        "extractA": a,
                        "extractB": b,
                        "demandA": demand_a or {},
                        "demandB": demand_b or {},
                        "demandRows": demand_rows or [],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        text = self._call_deepseek(prompt)
        return self._require_json(text)

    def draft_document(
        self,
        doc_type: str,
        case_context: dict[str, Any],
        agreed_terms: list[dict[str, Any]],
        analysis: dict[str, Any] | None = None,
        demand_rows: list[list[Any]] | None = None,
        rounds: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        title = "调解协议书" if doc_type == "MEDIATION_AGREEMENT" else "调解笔录"
        self._ensure_deepseek_available()
        if doc_type == "MEDIATION_AGREEMENT":
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "你是公安治安调解协议条款起草助手。只输出严格 JSON，不要解释。"
                        "任务：根据违法事实和双方诉求，生成“经调解，双方自愿达成如下协议”后面的协调内容。"
                        "只生成协议条款，不要输出标题、当事人基本信息、主要事实、生效条款、签名栏。"
                        "条款应分点描述，语气正式、明确、可履行，避免编造未给出的金额、期限和承诺。"
                        "凡涉及赔偿、补偿、医疗费、修理费等金额，必须同时写明小写数字和中文大写金额，格式如“500元（大写：伍佰元整）”。"
                        "输出字段必须为 title, clauses, summary。"
                        "clauses 必须是字符串数组，每条为一个完整协议事项，不要带“一、二、三”等序号。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "docType": doc_type,
                            "title": title,
                            "illegalFact": case_context.get("illegal_fact", ""),
                            "parties": case_context.get("parties", []),
                            "demandRows": demand_rows or [],
                            "agreedTerms": agreed_terms,
                            "analysis": analysis or {},
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            text = self._call_deepseek(prompt, temperature=0.15)
            result = self._require_json(text)
            clauses = result.get("clauses", [])
            if not isinstance(clauses, list):
                clauses = [str(clauses)] if clauses else []
            result["clauses"] = [_format_clause_amounts(str(clause)) for clause in clauses if str(clause).strip()]
            return result
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是公安治安调解笔录整理助手。只输出严格 JSON，不要解释。"
                    "任务：根据每个轮次的会谈转写，生成调解笔录中的调解内容。"
                    "输出字段必须为 title, statements, summary。"
                    "statements 必须是数组，每项包含 speaker 和 content。"
                    "按输入 rounds 的顺序整理，每个轮次对参与发言的每名当事人最多生成一条总结性陈述。"
                    "不要逐句复述原始转写，不要把寒暄、确认听清、重复追问等过程性对话写入笔录。"
                    "speaker 填真实当事人姓名；只有转写中确有主持人实质性陈述时才输出主持人。"
                    "content 用正式笔录口吻概括该轮当事人的核心诉求、事实说明、协商态度或不同意见。"
                    "同一轮内同一当事人多次发言整合成一段第一人称视角的陈述。"
                    "不能编造转写中没有的金额、期限、承诺或事实；信息不足时只概括已表达内容。"
                    "不要输出基本信息、签字栏，也不要输出最后的主持人固定告知语。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "docType": doc_type,
                        "title": title,
                        "caseContext": case_context,
                        "agreedTerms": agreed_terms,
                        "analysis": analysis or {},
                        "rounds": rounds or [],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        text = self._call_deepseek(prompt, temperature=0.15)
        result = self._require_json(text)
        statements = result.get("statements", [])
        if not isinstance(statements, list):
            result["statements"] = []
        return result
