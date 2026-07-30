from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import websockets

from ..core.config import get_settings


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
            # 开启说话人分离时自动切到支持声纹聚类的模型，避免用户只改参数忘了换引擎。
            engine_model_type = "16k_zh_en_speaker_2.0"
        params = {
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
        sign_str = "&".join(f"{k}={params[k]}" for k in sorted(params))
        # 按腾讯云实时语音识别 V2 规则，签名原文不包含 wss:// 协议头。
        sign_str = f"asr.cloud.tencent.com/asr/v2/{appid}?{sign_str}"
        signature = base64.b64encode(hmac.new(secret_key.encode(), sign_str.encode(), hashlib.sha1).digest()).decode()
        params["signature"] = signature
        return f"wss://asr.cloud.tencent.com/asr/v2/{appid}?{urlencode(params)}"

    def signed_stream_url(self) -> str:
        """生成一次实时语音识别 WebSocket 地址，每轮会谈都使用新的 voice_id。"""
        return self._sign_url(str(uuid4()))

    @staticmethod
    def normalize_message(raw: str) -> dict[str, Any]:
        """把腾讯云返回结构规整成前端容易渲染的句子事件。"""
        message = json.loads(raw)
        sentences = message.get("sentences")
        result = message.get("result")
        text = ""
        speaker_id = None
        sentence_id = None
        sentence_final = False

        if isinstance(sentences, dict):
            text = sentences.get("sentence") or sentences.get("text") or ""
            speaker_id = sentences.get("speaker_id")
            sentence_id = sentences.get("sentence_id")
            sentence_final = sentences.get("sentence_type") == 1
        elif isinstance(sentences, list) and sentences:
            item = sentences[-1]
            if isinstance(item, dict):
                text = item.get("sentence") or item.get("text") or ""
                speaker_id = item.get("speaker_id")
                sentence_id = item.get("sentence_id")
                sentence_final = item.get("sentence_type") == 1
        elif isinstance(result, dict):
            # 实时语音识别 V2 常见返回结构是 result.voice_text_str；
            # 前端只接收字符串，所以这里必须提前拆出来。
            text = result.get("voice_text_str") or result.get("sentence") or result.get("text") or ""
            voice_id = message.get("voice_id") or "voice"
            sentence_id = f"{voice_id}:{result.get('index')}"
            sentence_final = result.get("slice_type") == 2
            speaker_info = result.get("speaker_info")
            if isinstance(speaker_info, dict):
                speaker_id = speaker_info.get("speaker_id") or speaker_info.get("id")
            elif result.get("speaker_id") is not None:
                speaker_id = result.get("speaker_id")
        else:
            text = message.get("text") or ""

        code = message.get("code", 0)
        return {
            "type": "error" if code else ("sentence" if text else "status"),
            "code": code,
            "message": message.get("message", ""),
            "text": text,
            "speaker_id": speaker_id,
            "sentence_id": sentence_id,
            "is_final": bool(message.get("final") == 1 or sentence_final),
            "raw": message,
        }

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
