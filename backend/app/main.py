"""接口说明。"""

from __future__ import annotations

import contextlib
import asyncio
import json
import time
from typing import Any

import websockets
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core.config import get_settings
from .models import Utterance, new_id, now_iso
from .services.documents import make_document, render_document_text, touch_document
from .services.llm import LLMService
from .services.tencent_asr import DEBUG_LOG_PATH, get_asr_provider, write_asr_debug
from .state import CASE_STATE, audit


settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = settings.frontend_dir
app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

llm = LLMService()
asr = get_asr_provider()

TENCENT_PCM_CHUNK_BYTES = 1280
FIXED_DEMAND_TOPICS = ["道歉", "赔偿金额", "履行方式", "后续承诺", "其他"]


class ExtractRequest(BaseModel):
    session_key: str
    transcript: str
    current_extraction: dict[str, Any] | None = None
    current_demand: dict[str, Any] | None = None


class AnalyzeRequest(BaseModel):
    session_a: dict[str, Any]
    session_b: dict[str, Any]
    demand_a: dict[str, Any] | None = None
    demand_b: dict[str, Any] | None = None
    demand_rows: list[list[Any]] | None = None


class DraftRequest(BaseModel):
    doc_type: str
    agreed_terms: list[dict[str, Any]]
    analysis: dict[str, Any] | None = None
    demand_rows: list[list[Any]] | None = None
    rounds: list[dict[str, Any]] | None = None


class UtteranceRequest(BaseModel):
    speaker: str
    text: str


class ASRClientDebugRequest(BaseModel):
    event: str
    payload: dict[str, Any] = {}


def _normalize_demand_topic(topic: Any, content: Any = "") -> str:
    value = f"{topic or ''} {content or ''}".upper()
    if any(keyword in value for keyword in ["COMPENSATION", "赔偿", "金额", "费用", "医疗"]):
        return "赔偿金额"
    if any(keyword in value for keyword in ["APOLOGY", "道歉", "致歉"]):
        return "道歉"
    if any(keyword in value for keyword in ["PERFORM", "履行", "支付", "付清", "一次性", "当场", "期限"]):
        return "履行方式"
    if any(keyword in value for keyword in ["PROMISE", "承诺", "保证", "不再", "后续", "骚扰", "冲突"]):
        return "后续承诺"
    return "其他"


def _normalize_claim_item(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"content": item}
    if not isinstance(item, dict):
        item = {}
    content = str(item.get("content") or item.get("description") or item.get("statement") or "").strip() or "无"
    topic = _normalize_demand_topic(item.get("topic") or item.get("type") or "其他", content)
    normalized = dict(item)
    normalized["topic"] = topic
    normalized["content"] = content
    return normalized


def normalize_extraction_result(result: dict[str, Any]) -> dict[str, Any]:
    """兜底处理大模型未按要求返回“无”的诉求事项。"""
    if not isinstance(result, dict):
        return {"facts": [], "claims": [{"topic": topic, "content": "无"} for topic in FIXED_DEMAND_TOPICS], "concessions": [], "attitude": {}}

    normalized = dict(result)
    raw_claims = normalized.get("claims")
    if isinstance(raw_claims, list):
        claims = [_normalize_claim_item(item) for item in raw_claims]
    elif raw_claims:
        claims = [_normalize_claim_item(raw_claims)]
    else:
        claims = []

    by_topic: dict[str, dict[str, Any]] = {}
    for item in claims:
        topic = item["topic"]
        if topic not in by_topic or by_topic[topic].get("content") == "无":
            by_topic[topic] = item

    normalized["claims"] = [
        by_topic.get(topic, {"topic": topic, "content": "无"})
        for topic in FIXED_DEMAND_TOPICS
    ]
    normalized.setdefault("facts", [])
    normalized.setdefault("concessions", [])
    normalized.setdefault("attitude", {})
    return normalized


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """接口说明。"""
    return HTMLResponse((frontend_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    """接口说明。"""
    return CASE_STATE.to_dict()


@app.get("/api/config")
def get_public_config() -> dict[str, Any]:
    """接口说明。"""
    return {
        "asr_mode": settings.asr_mode,
        "tencent_asr_speaker_diarization": settings.tencent_asr_speaker_diarization,
        "tencent_asr_engine_model_type": settings.tencent_asr_effective_engine_model_type,
    }


@app.get("/api/asr/debug")
def get_asr_debug() -> dict[str, Any]:
    """接口说明。"""
    if not DEBUG_LOG_PATH.exists():
        return {"items": []}
    lines = DEBUG_LOG_PATH.read_text(encoding="utf-8").splitlines()[-80:]
    items = []
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError):
            items.append(json.loads(line))
    return {"items": items}


@app.post("/api/asr/debug")
def add_asr_debug(req: ASRClientDebugRequest) -> dict[str, Any]:
    """接口说明。"""
    write_asr_debug(f"client_{req.event}", req.payload)
    return {"ok": True}


@app.post("/api/asr/transcribe")
async def transcribe_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    """接口说明。"""
    audio_bytes = await file.read()
    try:
        chunks = await asr.transcribe_audio(audio_bytes)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    text = "\n".join(chunk.text for chunk in chunks if chunk.text)
    audit("system", "transcribe_audio", "session", f"audio={file.filename}")
    return {"chunks": [chunk.__dict__ for chunk in chunks], "text": text}


@app.websocket("/api/asr/realtime")
async def realtime_asr(websocket: WebSocket) -> None:
    """接口说明。"""
    await websocket.accept()

    try:
        tencent_url = asr.signed_stream_url()
        write_asr_debug(
            "connect_start",
            {
                "effective_engine_model_type": settings.tencent_asr_effective_engine_model_type,
                "speaker_diarization": settings.tencent_asr_speaker_diarization,
                "speaker_context": settings.tencent_asr_enable_speaker_context,
            },
        )
    except RuntimeError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011, reason="asr config error")
        return

    try:
        async with websockets.connect(tencent_url, ping_interval=None, max_size=None) as tencent_ws:
            # 腾讯云连接成功后会先返回握手状态，规整后通知前端。
            hello = asr.normalize_message(await tencent_ws.recv())
            write_asr_debug("upstream_hello", {"type": hello.get("type"), "code": hello.get("code"), "message": hello.get("message")})
            if hello.get("type") == "error":
                await websocket.send_json(hello)
                await websocket.close(code=1000)
                return
            await websocket.send_json({"type": "status", "message": hello.get("message") or "ASR connected"})

            final_result_received = asyncio.Event()
            audio_buffer = bytearray()
            audio_stats = {"browser_bytes": 0, "upstream_chunks": 0, "upstream_bytes": 0, "sentences": 0, "statuses": 0}

            async def send_pcm_to_tencent(data: bytes, *, flush: bool = False) -> None:
                """接口说明。"""
                audio_stats["browser_bytes"] += len(data)
                audio_buffer.extend(data)
                while len(audio_buffer) >= TENCENT_PCM_CHUNK_BYTES:
                    chunk = bytes(audio_buffer[:TENCENT_PCM_CHUNK_BYTES])
                    del audio_buffer[:TENCENT_PCM_CHUNK_BYTES]
                    await tencent_ws.send(chunk)
                    audio_stats["upstream_chunks"] += 1
                    audio_stats["upstream_bytes"] += len(chunk)
                if flush and audio_buffer:
                    chunk = bytes(audio_buffer)
                    await tencent_ws.send(chunk)
                    audio_stats["upstream_chunks"] += 1
                    audio_stats["upstream_bytes"] += len(chunk)
                    audio_buffer.clear()

            async def forward_tencent_result() -> None:
                try:
                    async for raw_message in tencent_ws:
                        should_stop = False
                        for payload in asr.normalize_messages(raw_message):
                            if payload.get("type") == "sentence":
                                audio_stats["sentences"] += 1
                            else:
                                audio_stats["statuses"] += 1
                            raw_payload = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
                            write_asr_debug(
                                "upstream_message",
                                {
                                    "type": payload.get("type"),
                                    "code": payload.get("code"),
                                    "message": payload.get("message"),
                                    "has_text": bool(payload.get("text")),
                                    "speaker_id": payload.get("speaker_id"),
                                    "is_final": payload.get("is_final"),
                                    "stream_final": payload.get("stream_final"),
                                    "raw_keys": sorted(raw_payload.keys()),
                                },
                            )
                            await websocket.send_json(payload)
                            if payload.get("type") == "error" or payload.get("stream_final"):
                                should_stop = True
                        if should_stop:
                            break
                except websockets.ConnectionClosed:
                    pass
                finally:
                    final_result_received.set()

            forward_task = asyncio.create_task(forward_tencent_result())
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        await send_pcm_to_tencent(message["bytes"])
                    elif message.get("text"):
                        payload = json.loads(message["text"])
                        if payload.get("type") == "end":
                            with contextlib.suppress(Exception):
                                await send_pcm_to_tencent(b"", flush=True)
                                await tencent_ws.send(json.dumps({"type": "end"}, ensure_ascii=False))
                            with contextlib.suppress(asyncio.TimeoutError):
                                await asyncio.wait_for(final_result_received.wait(), timeout=1)
                            break
            finally:
                if not forward_task.done():
                    forward_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await forward_task
                write_asr_debug("stream_closed", audio_stats)
                audit("system", "realtime_asr", "session", "stream closed")
                with contextlib.suppress(Exception):
                    await websocket.close(code=1000)
    except WebSocketDisconnect:
        audit("system", "realtime_asr", "session", "client disconnected")
        with contextlib.suppress(Exception):
            await websocket.close(code=1000)
    except websockets.ConnectionClosed:
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "status", "message": "ASR upstream closed"})
            await websocket.close(code=1001)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1011, reason="asr bridge error")


@app.post("/api/sessions/{session_key}/utterances")
def add_utterance(session_key: str, req: UtteranceRequest) -> dict[str, Any]:
    """接口说明。"""
    session = CASE_STATE.sessions.get(session_key)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.utterances.append(Utterance(id=new_id("utt"), speaker=req.speaker, text=req.text))
    session.transcript_text = "\n".join(f"{item.speaker}: {item.text}" for item in session.utterances)
    audit("user", "add_utterance", session.id, req.text[:120])
    return {"session": session.__dict__}


@app.post("/api/ai/extract")
def extract(req: ExtractRequest) -> dict[str, Any]:
    """接口说明。"""
    started_at = time.perf_counter()
    # 提取阶段只传当前当事人上下文，避免双方诉求相互污染。
    case_context = CASE_STATE.to_dict()
    current_session = case_context.get("sessions", {}).get(req.session_key, {})
    case_context["sessions"] = {
        req.session_key: {
            "type": current_session.get("type", ""),
            "speaker": current_session.get("speaker", ""),
        }
    }
    case_context["audit_log"] = []
    try:
        result = llm.extract(
            req.transcript,
            case_context,
            current_extraction=req.current_extraction or {},
            current_demand=req.current_demand or {},
        )
        result = normalize_extraction_result(result)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session = CASE_STATE.sessions.get(req.session_key)
    if session:
        session.transcript_text = req.transcript
        session.extraction = result
        audit("ai", "extract", session.id, "generated extraction")
    result["_meta"] = {"elapsed_ms": round((time.perf_counter() - started_at) * 1000)}
    return result


@app.post("/api/ai/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    """接口说明。"""
    started_at = time.perf_counter()
    try:
        result = llm.analyze(
            req.session_a,
            req.session_b,
            CASE_STATE.to_dict(),
            demand_a=req.demand_a or {},
            demand_b=req.demand_b or {},
            demand_rows=req.demand_rows or [],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit("ai", "analyze", CASE_STATE.id, "generated analysis")
    result["_meta"] = {"elapsed_ms": round((time.perf_counter() - started_at) * 1000)}
    return result


@app.post("/api/documents/draft")
def draft(req: DraftRequest) -> dict[str, Any]:
    """接口说明。"""
    title_map = {
        "MEDIATION_AGREEMENT": "调解协议书",
        "MEDIATION_RECORD": "调解笔录",
    }
    title = title_map.get(req.doc_type, "调解文书")
    try:
        content = llm.draft_document(
            req.doc_type,
            CASE_STATE.to_dict(),
            req.agreed_terms,
            req.analysis,
            demand_rows=req.demand_rows or [],
            rounds=req.rounds or [],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    doc = make_document(req.doc_type, title, content)
    CASE_STATE.documents[doc.id] = doc
    audit("ai", "draft_document", doc.id, title)
    return {"document": doc.__dict__, "text": render_document_text(doc)}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    """接口说明。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document": doc.__dict__, "text": render_document_text(doc)}


@app.patch("/api/documents/{doc_id}")
def patch_document(doc_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """接口说明。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    touch_document(doc, patch)
    audit("user", "patch_document", doc.id, "document edited")
    return {"document": doc.__dict__, "text": render_document_text(doc)}


@app.post("/api/documents/{doc_id}/finalize")
def finalize_document(doc_id: str) -> dict[str, Any]:
    """接口说明。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.status = "FINALIZED"
    doc.updated_at = now_iso()
    audit("user", "finalize_document", doc.id, "document finalized")
    return {"document": doc.__dict__, "hash": doc.content_hash}


@app.get("/api/documents/{doc_id}/export")
def export_document(doc_id: str) -> PlainTextResponse:
    """接口说明。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return PlainTextResponse(render_document_text(doc))


@app.get("/api/audit")
def audit_log() -> dict[str, Any]:
    """接口说明。"""
    return {"items": CASE_STATE.audit_log}


def _static_file(name: str) -> FileResponse:
    """接口说明。"""
    return FileResponse(frontend_dir / name)


@app.get("/styles.css")
def styles() -> FileResponse:
    return _static_file("styles.css")


@app.get("/app.js")
def app_js() -> FileResponse:
    return _static_file("app.js")


