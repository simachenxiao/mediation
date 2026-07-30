"""调解工作台后端入口。"""

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
TENCENT_PCM_CHUNK_INTERVAL_SECONDS = 0.04


SIMULATED_REALTIME_SCRIPTS: dict[str, list[list[dict[str, str]]]] = {
    "a": [
        [
            {"speaker": "赵武", "role": "police", "text": "李江，周枫表示愿意道歉和赔偿。你这边什么意见？"},
            {"speaker": "李江", "role": "party-a", "text": "他拿扳手砸我脑袋，砸得我头破血流，还骂我黑店。我开店做生意，不是让他撒野的。少于两万我不谈。"},
            {"speaker": "钱柳", "role": "police", "text": "你的诉求我们会转达。除了赔偿，还有其他要求吗？"},
            {"speaker": "李江", "role": "party-a", "text": "他必须认错，别再来我店里闹，也别到处胡说我坑他钱。"},
        ],
        [
            {"speaker": "赵武", "role": "police", "text": "周枫提出赔偿八千元，今天先付六千，剩余两千在2026年07月25日18时前付清，并向你道歉，承诺不再到店里闹、不再乱说你的店。"},
            {"speaker": "李江", "role": "party-a", "text": "八千还是便宜他了。他拿扳手敲我脑袋，三千六千地往外挤，真他妈会算。"},
            {"speaker": "钱柳", "role": "police", "text": "你是否接受这个调整后的方案？"},
            {"speaker": "李江", "role": "party-a", "text": "钱今天必须先到账，道歉也得说清楚，是他骂我黑店，是他拿扳手打我头。剩下两千要写死时间。"},
            {"speaker": "赵武", "role": "police", "text": "这些都可以确认。这样是否同意？"},
            {"speaker": "李江", "role": "party-a", "text": "行。六千到账，剩下两千2026年07月25日18点前给，他以后别再犯浑，我同意。"},
        ],
        [
            {"speaker": "赵武", "role": "police", "text": "李江，周枫已经支付六千元，你核对一下。"},
            {"speaker": "李江", "role": "party-a", "text": "到账了。"},
            {"speaker": "钱柳", "role": "police", "text": "周枫确认剩余两千元在2026年07月25日18时前付清，并作出了道歉和承诺。你是否接受？"},
            {"speaker": "李江", "role": "party-a", "text": "接受。钱按时给，以后别再来我店里撒野，这事我就按调解走。"},
            {"speaker": "赵武", "role": "police", "text": "双方意见一致。"},
        ],
    ],
    "b": [
        [
            {"speaker": "赵武", "role": "police", "text": "李江要求你赔偿两万元，并且道歉，承诺不再滋扰店铺。"},
            {"speaker": "周枫", "role": "party-b", "text": "两万太高了，我真拿不出。我承认我打人不对，也愿意道歉。我先赔三千行不行？"},
            {"speaker": "钱柳", "role": "police", "text": "你用金属扳手打的是头部，三千和对方诉求差距太大。"},
            {"speaker": "周枫", "role": "party-b", "text": "那我提高到六千，今天可以付。"},
            {"speaker": "赵武", "role": "police", "text": "我们转达，但对方未必接受。你还有没有余地？"},
            {"speaker": "周枫", "role": "party-b", "text": "最多八千。今天付六千，剩下两千三天内付清。"},
        ],
        [
            {"speaker": "赵武", "role": "police", "text": "李江同意八千元方案，但要求今天六千元到账，剩余两千元在2026年07月25日18时前付清，并明确道歉和承诺。"},
            {"speaker": "周枫", "role": "party-b", "text": "我同意。我现在转六千元，剩下两千按时付。"},
            {"speaker": "钱柳", "role": "police", "text": "道歉内容你再确认。"},
            {"speaker": "周枫", "role": "party-b", "text": "我不该因为电动车维修问题骂李江的店是黑店，也不该拿金属扳手打他的头。我认错，向他道歉。我保证以后不再因为这件事去店里闹，也不再乱说损害他和店铺名声的话。"},
        ],
    ],
}

SIMULATED_REALTIME_SEQUENCE = ["a", "b", "a", "b", "a"]


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


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """直接返回前端原型页面。"""
    return HTMLResponse((frontend_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    """给前端返回案件快照。"""
    return CASE_STATE.to_dict()


@app.get("/api/config")
def get_public_config() -> dict[str, Any]:
    """返回前端需要知道的非敏感运行配置。"""
    return {
        "realtime_asr_simulation": settings.realtime_asr_simulation,
        "realtime_asr_simulation_sequence": SIMULATED_REALTIME_SEQUENCE,
        "tencent_asr_speaker_diarization": settings.tencent_asr_speaker_diarization,
        "tencent_asr_engine_model_type": settings.tencent_asr_engine_model_type,
    }


@app.get("/api/asr/debug")
def get_asr_debug() -> dict[str, Any]:
    """读取最近的 ASR 脱敏调试日志，便于定位是否收到音频和腾讯返回。"""
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
    """接收前端 ASR 调试信息，例如浏览器侧音量和 WebSocket 状态。"""
    write_asr_debug(f"client_{req.event}", req.payload)
    return {"ok": True}


@app.post("/api/asr/transcribe")
async def transcribe_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    """把上传的音频交给 ASR Provider 处理。"""
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
    """浏览器推送 16k PCM 音频分片，后端桥接腾讯云实时语音识别。"""
    await websocket.accept()

    if settings.realtime_asr_simulation:
        try:
            round_index = int(websocket.query_params.get("round", "1"))
        except ValueError:
            round_index = 1
        await _simulate_realtime_asr(websocket, websocket.query_params.get("session", "a"), round_index)
        return

    try:
        tencent_url = asr.signed_stream_url()
        write_asr_debug(
            "connect_start",
            {
                "engine_model_type": settings.tencent_asr_engine_model_type,
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
                """按腾讯云建议的 16k PCM 40ms/1280 字节节奏转发音频，避免上游因包过大不出结果。"""
                audio_stats["browser_bytes"] += len(data)
                audio_buffer.extend(data)
                while len(audio_buffer) >= TENCENT_PCM_CHUNK_BYTES:
                    chunk = bytes(audio_buffer[:TENCENT_PCM_CHUNK_BYTES])
                    del audio_buffer[:TENCENT_PCM_CHUNK_BYTES]
                    await tencent_ws.send(chunk)
                    audio_stats["upstream_chunks"] += 1
                    audio_stats["upstream_bytes"] += len(chunk)
                    await asyncio.sleep(TENCENT_PCM_CHUNK_INTERVAL_SECONDS)
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
                                await asyncio.wait_for(final_result_received.wait(), timeout=8)
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


async def _simulate_realtime_asr(websocket: WebSocket, session_key: str, round_index: int) -> None:
    """按配置开关推送固定会谈脚本，用于端到端流程测试。"""
    scripts = SIMULATED_REALTIME_SCRIPTS.get(session_key) or SIMULATED_REALTIME_SCRIPTS["a"]
    script = scripts[min(max(round_index, 1), len(scripts)) - 1]
    interval = max(settings.realtime_asr_simulation_interval_ms, 0) / 1000
    await websocket.send_json(
        {"type": "status", "message": "ASR simulation enabled", "session": session_key, "round": round_index, "simulation": True}
    )

    async def send_script() -> None:
        for index, item in enumerate(script, start=1):
            await asyncio.sleep(interval)
            await websocket.send_json(
                {
                    "type": "sentence",
                    "sentence_id": f"sim-{session_key}-{round_index}-{index}",
                    "speaker": item["speaker"],
                    "role": item["role"],
                    "text": item["text"],
                    "is_final": True,
                    "simulation": True,
                }
            )
        await websocket.send_json({"type": "status", "message": "ASR simulation completed", "simulation": True})

    sender = asyncio.create_task(send_script())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("text"):
                payload = json.loads(message["text"])
                if payload.get("type") == "end":
                    break
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        audit("system", "realtime_asr_simulation", session_key, "stream closed")
        with contextlib.suppress(Exception):
            await websocket.close(code=1000)


@app.post("/api/sessions/{session_key}/utterances")
def add_utterance(session_key: str, req: UtteranceRequest) -> dict[str, Any]:
    """追加一条会话发言并刷新整段转写文本。"""
    session = CASE_STATE.sessions.get(session_key)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.utterances.append(Utterance(id=new_id("utt"), speaker=req.speaker, text=req.text))
    session.transcript_text = "\n".join(f"{item.speaker}: {item.text}" for item in session.utterances)
    audit("user", "add_utterance", session.id, req.text[:120])
    return {"session": session.__dict__}


@app.post("/api/ai/extract")
def extract(req: ExtractRequest) -> dict[str, Any]:
    """提炼诉求和事实结构。"""
    started_at = time.perf_counter()
    # 提取阶段只给模型案件基础信息和当前当事人身份，不携带另一方转写/诉求，避免双方信息相互污染。
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
    """对双方提炼结果做对比分析。"""
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
    """生成调解协议书或调解笔录草稿。"""
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
    """读取单个文书。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document": doc.__dict__, "text": render_document_text(doc)}


@app.patch("/api/documents/{doc_id}")
def patch_document(doc_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """回写前端编辑后的文书内容。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    touch_document(doc, patch)
    audit("user", "patch_document", doc.id, "document edited")
    return {"document": doc.__dict__, "text": render_document_text(doc)}


@app.post("/api/documents/{doc_id}/finalize")
def finalize_document(doc_id: str) -> dict[str, Any]:
    """把文书状态切到定稿。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.status = "FINALIZED"
    doc.updated_at = now_iso()
    audit("user", "finalize_document", doc.id, "document finalized")
    return {"document": doc.__dict__, "hash": doc.content_hash}


@app.get("/api/documents/{doc_id}/export")
def export_document(doc_id: str) -> PlainTextResponse:
    """导出文书纯文本。"""
    doc = CASE_STATE.documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return PlainTextResponse(render_document_text(doc))


@app.get("/api/audit")
def audit_log() -> dict[str, Any]:
    """返回审计日志。"""
    return {"items": CASE_STATE.audit_log}


def _static_file(name: str) -> FileResponse:
    """统一托管前端静态资源。"""
    return FileResponse(frontend_dir / name)


@app.get("/styles.css")
def styles() -> FileResponse:
    return _static_file("styles.css")


@app.get("/app.js")
def app_js() -> FileResponse:
    return _static_file("app.js")
