from __future__ import annotations

from hashlib import sha256

from .models import CaseState, Party, Session, new_id, now_iso


def build_case_state() -> CaseState:
    parties = [
        Party(
            id=new_id("party"),
            name="\u674e\u6c5f",
            role="\u53d7\u5bb3\u4eba",
            gender="\u7537",
            id_no="330108********2134",
            identity="\u62a5\u6848\u4eba / \u53d7\u5bb3\u4eba",
            phone="13800001111",
            work_unit="/",
            occupation="/",
            home_address="/",
        ),
        Party(
            id=new_id("party"),
            name="\u5468\u67ab",
            role="\u5acc\u7591\u4eba",
            gender="\u7537",
            id_no="330108********7788",
            identity="\u5acc\u7591\u4eba",
            phone="13900002222",
            work_unit="/",
            occupation="/",
            home_address="/",
        ),
    ]

    sessions = {
        "a": Session(
            id=new_id("session"),
            type="SEPARATE_A",
            speaker="\u674e\u6c5f",
            started_at=now_iso(),
        ),
        "b": Session(
            id=new_id("session"),
            type="SEPARATE_B",
            speaker="\u5468\u67ab",
            started_at=now_iso(),
        ),
    }

    return CaseState(
        id=new_id("case"),
        case_no="A20260722-016",
        title="\u6545\u610f\u4f24\u5bb3\u8c03\u89e3",
        category="\u6cbb\u5b89\u8c03\u89e3",
        status="\u4f1a\u8c08\u5bf9\u7167",
        location="\u676d\u5dde\u5e02\u6ee8\u6c5f\u533a",
        occurred_at="2026-07-22 16:50",
        created_by="\u8d75\u6b66\u3001\u94b1\u654f",
        parties=parties,
        sessions=sessions,
        case_cause="\u6bb4\u6253\u4ed6\u4eba",
        illegal_fact=(
            "2026 \u5e74 07 \u6708 22 \u65e5 16 \u65f6 30 \u5206\u8bb8\uff0c"
            "\u5728\u676d\u5dde\u5e02\u6ee8\u6c5f\u533a\u6c5f\u9675\u8def 21 \u53f7\u4e07\u4e8b\u8fbe\u7535\u52a8\u8f66\u4fee\u7406\u5e97\u5185\uff0c"
            "\u5468\u67ab\u56e0\u7535\u52a8\u8f66\u7ef4\u4fee\u4e89\u8bae\u6301\u91d1\u5c5e\u6273\u624b\u51fb\u6253\u674e\u6c5f\u5934\u90e8\uff0c"
            "\u81f4\u674e\u6c5f\u5934\u76ae\u632b\u88c2\u4f24\uff0c\u7ecf\u9274\u5b9a\u4e3a\u8f7b\u5fae\u4f24\u3002"
        ),
        documents={},
        audit_log=[],
    )


CASE_STATE = build_case_state()


def hash_text(*parts: str) -> str:
    # 用于压缩多段文本，生成稳定哈希。
    h = sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
    return h.hexdigest()


def audit(actor: str, action: str, target: str, detail: str) -> None:
    # 关键操作都写入审计日志，便于回溯流程。
    CASE_STATE.audit_log.append(
        {
            "at": now_iso(),
            "actor": actor,
            "action": action,
            "target": target,
            "detail": detail,
        }
    )
