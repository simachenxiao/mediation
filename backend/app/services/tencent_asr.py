from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import websockets

from ..core.config import get_settings


DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "asr-debug.log"


def write_asr_debug(event: str, payload: dict[str, Any]) -> None:
    """写入 ASR 调试日志，避免把密钥和签名输出到日志里。"""
    DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": round(time.time(), 3), "event": event, **payload}
    with DEBUG_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


@dataclass
class ASRChunk:
    text: str
    is_final: bool = False
    start_ms: int = 0
    end_ms: int = 0


class TencentASRProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _sign_url(self, voice_id: str | None = None) -> str:
        appid = self.settings.tencent_asr_appid
        secret_id = self.settings.tencent_asr_secret_id
        secret_key = self.settings.tencent_asr_secret_key
        if not secret_id or not secret_key:
            raise RuntimeError("Tencent ASR secret id/key is missing")

        timestamp = int(time.time())
        expired = timestamp + 60 * 30
        voice_id = voice_id or str(uuid4())
        engine_model_type = self.settings.tencent_asr_engine_model_type
        if self.settings.tencent_asr_speaker_diarization and "speaker" not in engine_model_type:
            # 开启说话人分离时自动切到支持该能力的模型，避免只改开关忘记换模型。
            engine_model_type = "16k_zh_en_speaker_2.0"

        params: dict[str, Any] = {
            "engine_model_type": engine_model_type,
            "expired": expired,
            "nonce": random.randint(100000, 9999999999),
            "secretid": secret_id,
            "timestamp": timestamp,
            "voice_id": voice_id,
            "voice_format": 1,
            "needvad": 1,
        }
        if self.settings.tencent_asr_speaker_diarization:
            params["speaker_diarization"] = 1
            if self.settings.tencent_asr_enable_speaker_context:
                params["enable_speaker_context"] = 1
            if self.settings.tencent_asr_speaker_context_id:
                params["speaker_context_id"] = self.settings.tencent_asr_speaker_context_id

        sign_str = "&".join(f"{key}={params[key]}" for key in sorted(params))
        # 按腾讯云实时语音识别 V2 规则，签名原文不包含 wss:// 协议头。
        sign_str = f"asr.cloud.tencent.com/asr/v2/{appid}?{sign_str}"
        signature = base64.b64encode(hmac.new(secret_key.encode(), sign_str.encode(), hashlib.sha1).digest()).decode()
        params["signature"] = signature
        return f"wss://asr.cloud.tencent.com/asr/v2/{appid}?{urlencode(params)}"

    def signed_stream_url(self) -> str:
        """生成一次实时语音识别 WebSocket 地址，每轮会谈都使用新的 voice_id。"""
        return self._sign_url(str(uuid4()))

    @staticmethod
    def _read_sentence_item(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            sentence_list = item.get("sentence_list")
            if isinstance(sentence_list, list):
                for sentence in reversed(sentence_list):
                    parsed = TencentASRProvider._read_sentence_item(sentence)
                    if parsed["text"]:
                        return parsed
                return TencentASRProvider._read_sentence_item(sentence_list[-1] if sentence_list else {})
            return {
                "text": item.get("sentence") or item.get("text") or item.get("voice_text_str") or "",
                "speaker_id": item.get("speaker_id"),
                "sentence_id": item.get("sentence_id") or item.get("index"),
                "is_final": item.get("sentence_type") == 1 or item.get("slice_type") == 2,
            }
        if isinstance(item, str):
            # 文档示例里 sentences 可能表现为 "speaker_id:0: text:... sentence_id:1 ..." 这类文本。
            speaker_match = re.search(r"speaker_id\s*:\s*(-?\d+)", item)
            sentence_match = re.search(r"sentence_id\s*:\s*(\d+)", item)
            text_match = re.search(
                r"text\s*:\s*(.*?)(?:\s+sentence_id\s*:|\s+start_time\s*:|\s+end_time\s*:|\s+sentence_type\s*:|$)",
                item,
            )
            type_match = re.search(r"sentence_type\s*:\s*(\d+)", item)
            return {
                "text": (text_match.group(1).strip() if text_match else item.strip()),
                "speaker_id": int(speaker_match.group(1)) if speaker_match else None,
                "sentence_id": int(sentence_match.group(1)) if sentence_match else None,
                "is_final": type_match.group(1) == "1" if type_match else False,
            }
        return {"text": "", "speaker_id": None, "sentence_id": None, "is_final": False}

    @classmethod
    def _read_sentences(cls, sentences: Any) -> dict[str, Any]:
        if isinstance(sentences, list):
            # 腾讯的人声分离可能返回句子快照列表；优先取最后一条有文本的句子。
            for item in reversed(sentences):
                parsed = cls._read_sentence_item(item)
                if parsed["text"]:
                    return parsed
            return cls._read_sentence_item(sentences[-1] if sentences else {})
        return cls._read_sentence_item(sentences)

    @classmethod
    def _read_sentence_events(cls, sentences: Any) -> list[dict[str, Any]]:
        if isinstance(sentences, dict) and isinstance(sentences.get("sentence_list"), list):
            return [cls._read_sentence_item(item) for item in sentences["sentence_list"]]
        if isinstance(sentences, list):
            return [cls._read_sentence_item(item) for item in sentences]
        return [cls._read_sentence_item(sentences)]

    @staticmethod
    def _build_payload(message: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
        text = parsed.get("text") or ""
        code = message.get("code", 0)
        has_error = code not in {0, "0", None}
        return {
            "type": "error" if has_error else ("sentence" if text else "status"),
            "code": code,
            "message": message.get("message") or message.get("error_msg") or "",
            "text": text,
            "speaker_id": parsed.get("speaker_id"),
            "sentence_id": parsed.get("sentence_id"),
            "is_final": bool(message.get("final") == 1 or parsed.get("is_final")),
            "stream_final": bool(message.get("final") == 1),
            "raw": message,
        }

    @classmethod
    def normalize_message(cls, raw: str) -> dict[str, Any]:
        """把腾讯云返回结构规整成前端容易渲染的句子事件。"""
        return cls.normalize_messages(raw)[-1]

    @classmethod
    def normalize_messages(cls, raw: str) -> list[dict[str, Any]]:
        """把腾讯云一次返回拆成一个或多个前端句子事件。"""
        message = json.loads(raw)
        sentences = message.get("sentences")
        result = message.get("result")

        if sentences is not None:
            payloads = [
                cls._build_payload(message, parsed)
                for parsed in cls._read_sentence_events(sentences)
                if parsed.get("text")
            ]
            return payloads or [cls._build_payload(message, {"text": "", "speaker_id": None, "sentence_id": None, "is_final": False})]
        if isinstance(result, dict):
            # 普通实时识别常见返回是 result.voice_text_str；这里继续保留兼容。
            text = result.get("voice_text_str") or result.get("sentence") or result.get("text") or ""
            voice_id = message.get("voice_id") or "voice"
            speaker_info = result.get("speaker_info")
            speaker_id = None
            if isinstance(speaker_info, dict):
                speaker_id = speaker_info.get("speaker_id") or speaker_info.get("id")
            elif result.get("speaker_id") is not None:
                speaker_id = result.get("speaker_id")
            return [
                cls._build_payload(
                    message,
                    {
                        "text": text,
                        "speaker_id": speaker_id,
                        "sentence_id": f"{voice_id}:{result.get('index')}",
                        "is_final": result.get("slice_type") == 2,
                    },
                )
            ]
        return [
            cls._build_payload(
                message,
                {"text": message.get("text") or "", "speaker_id": None, "sentence_id": None, "is_final": False},
            )
        ]

    async def transcribe_audio(self, audio_bytes: bytes) -> list[ASRChunk]:
        if not self.settings.tencent_asr_secret_id or not self.settings.tencent_asr_secret_key:
            raise RuntimeError("Tencent ASR secret id/key is missing")

        url = self._sign_url()
        async with websockets.connect(url, ping_interval=None) as ws:
            await ws.recv()  # 等待腾讯云握手成功后再发送音频。
            await ws.send(audio_bytes)
            await ws.send(json.dumps({"type": "end"}, ensure_ascii=False))
            result: list[ASRChunk] = []
            while True:
                message = self.normalize_message(await ws.recv())
                if message.get("code", 0) != 0:
                    raise RuntimeError(message.get("message", "Tencent ASR failed"))
                if message["text"]:
                    result.append(ASRChunk(text=message["text"], is_final=message["is_final"]))
                if message["is_final"]:
                    break
            return result


def get_asr_provider() -> TencentASRProvider:
    settings = get_settings()
    if settings.asr_provider == "tencent":
        return TencentASRProvider()
    raise RuntimeError(f"Unsupported ASR provider: {settings.asr_provider}")
