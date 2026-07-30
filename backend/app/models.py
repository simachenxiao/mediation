from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4


Confidence = Literal["HIGH", "MEDIUM", "LOW"]
DocStatus = Literal["DRAFT", "FINALIZED", "VOIDED"]
DocType = Literal["MEDIATION_RECORD", "MEDIATION_AGREEMENT"]


def now_iso() -> str:  # 统一时间格式，便于日志和前后端对齐。
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:  # 用前缀 + 随机后缀生成业务 ID。
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass
class Party:
    id: str
    name: str
    role: str
    gender: str
    id_no: str
    identity: str
    phone: str = ""
    work_unit: str = "/"
    occupation: str = "/"
    home_address: str = "/"


@dataclass
class Utterance:
    id: str
    speaker: str
    text: str
    start_ms: int = 0
    end_ms: int = 0
    corrected: bool = False


@dataclass
class Session:
    id: str
    type: str
    speaker: str
    started_at: str
    ended_at: str = ""
    audio_ref: str = ""
    utterances: list[Utterance] = field(default_factory=list)
    transcript_text: str = ""
    extraction: dict[str, Any] = field(default_factory=dict)


@dataclass
class Document:
    id: str
    type: DocType
    title: str
    status: DocStatus = "DRAFT"
    version: int = 1
    content: dict[str, Any] = field(default_factory=dict)
    edit_history: list[dict[str, Any]] = field(default_factory=list)
    content_hash: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class CaseState:
    id: str
    case_no: str
    title: str
    category: str
    status: str
    location: str
    occurred_at: str
    created_by: str
    parties: list[Party]
    sessions: dict[str, Session]
    case_cause: str = ""
    illegal_fact: str = ""
    documents: dict[str, Document] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # 给前端和大模型的统一快照。
