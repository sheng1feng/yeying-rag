from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.models import EmbeddingRecord, ImportedChunk, ImportedDocument, KnowledgeBase
from knowledge.services.filetypes import infer_file_type


router = APIRouter(tags=["documents"])


@router.get("/kbs/{kb_id}/documents")
def list_documents(kb_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[dict]:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    documents = db.scalars(select(ImportedDocument).where(ImportedDocument.kb_id == kb_id).order_by(ImportedDocument.updated_at.desc())).all()
    return [
        {
            "id": document.id,
            "source_path": document.source_path,
            "source_file_name": document.source_file_name,
            "file_type": infer_file_type(document.source_file_name),
            "source_kind": document.source_kind,
            "parse_status": document.parse_status,
            "chunk_count": document.chunk_count,
            "last_indexed_at": document.last_indexed_at,
        }
        for document in documents
    ]


@router.get("/kbs/{kb_id}/documents/{doc_id}")
def get_document(kb_id: int, doc_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    document = db.get(ImportedDocument, doc_id)
    if document is None or document.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="document not found")
    chunks = db.scalars(
        select(ImportedChunk)
        .options(selectinload(ImportedChunk.embedding))
        .where(ImportedChunk.document_id == doc_id)
        .order_by(ImportedChunk.chunk_index.asc())
    ).all()
    return {
        "id": document.id,
        "source_path": document.source_path,
        "source_file_name": document.source_file_name,
        "file_type": infer_file_type(document.source_file_name),
        "source_kind": document.source_kind,
        "parse_status": document.parse_status,
        "chunk_count": document.chunk_count,
        "last_indexed_at": document.last_indexed_at,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "source_etag_or_mtime": document.source_etag_or_mtime,
        "chunks": [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "metadata": chunk.metadata_json,
                "created_at": chunk.created_at,
                "embedding_model": chunk.embedding.embedding_model if chunk.embedding else None,
                "index_status": chunk.embedding.index_status if chunk.embedding else None,
            }
            for chunk in chunks
        ],
    }


@router.delete("/kbs/{kb_id}/documents/{doc_id}")
def delete_document(kb_id: int, doc_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    document = db.get(ImportedDocument, doc_id)
    if document is None or document.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="document not found")
    chunk_ids = [row[0] for row in db.execute(select(ImportedChunk.id).where(ImportedChunk.document_id == doc_id)).all()]
    if chunk_ids:
        db.execute(delete(EmbeddingRecord).where(EmbeddingRecord.chunk_id.in_(chunk_ids)))
        db.execute(delete(ImportedChunk).where(ImportedChunk.id.in_(chunk_ids)))
    db.delete(document)
    db.commit()
    return {"ok": True}
