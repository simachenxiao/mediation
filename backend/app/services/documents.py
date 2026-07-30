from __future__ import annotations

from hashlib import sha256
from typing import Any

from ..models import Document, new_id, now_iso


def render_document_text(document: Document) -> str:
    content = document.content  # 文书正文统一从结构化内容里渲染。
    lines = [f"# {content.get('title', document.title)}", ""]
    clauses = content.get("clauses", [])
    for clause in clauses:
        if isinstance(clause, dict):
            lines.append(f"- {clause.get('item', '')}: {clause.get('value', '')}")
        else:
            lines.append(f"- {clause}")
    if content.get("summary"):
        lines.extend(["", f"摘要：{content['summary']}"])
    return "\n".join(lines)


def make_document(doc_type: str, title: str, content: dict[str, Any]) -> Document:
    doc = Document(id=new_id("doc"), type=doc_type, title=title, content=content)  # 新建时就计算正文哈希。
    doc.content_hash = sha256(render_document_text(doc).encode("utf-8")).hexdigest()
    return doc


def touch_document(document: Document, patch: dict[str, Any]) -> Document:
    document.content.update(patch)  # 任何编辑都会写入版本和时间戳。
    document.version += 1
    document.updated_at = now_iso()
    document.edit_history.append({"at": document.updated_at, "patch": patch})
    document.content_hash = sha256(render_document_text(document).encode("utf-8")).hexdigest()
    return document

