from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.retrieval import (
    BotRetrievalContextRequest,
    BotRetrievalContextResponse,
    ContextAssembleRequest,
    ContextAssembleResponse,
    KnowledgeRetrieveRequest,
    KnowledgeRetrieveResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    MemoryRecallResponse,
    RecallMemoryRequest,
    RetrievalContextRequest,
    RetrievalContextResponse,
)
from knowledge.services.retrieval import RetrievalService


router = APIRouter(tags=["retrieval", "bot"])
service = RetrievalService()


def _bot_request_context(payload: BotRetrievalContextRequest) -> dict:
    conversation = payload.model_dump(
        include={"session_id", "conversation_id", "memory_namespace", "scene", "intent"},
        exclude_none=True,
    )
    return {
        "user_identity": payload.user_identity,
        "session_id": payload.session_id,
        "memory_namespace": payload.memory_namespace,
        "conversation_id": payload.conversation_id,
        "bot_id": payload.bot_id,
        "app_id": payload.app_id,
        "intent": payload.intent,
        "scene": payload.scene,
        "conversation": conversation,
        "caller": {
            "app_name": payload.app_id,
            "request_id": None,
        },
        "legacy_labels": {
            "bot_id": payload.bot_id,
            "app_id": payload.app_id,
        },
    }


def _build_neutral_context_response(payload: RetrievalContextRequest, result: dict) -> dict:
    response = {
        "query": payload.query,
        "conversation": payload.conversation.model_dump(exclude_none=True),
        "scope": payload.scope.model_dump(),
        "caller": payload.caller.model_dump(exclude_none=True),
        "knowledge": {
            "hits": result["knowledge_hits"],
            "source_refs": result["source_refs"],
        },
        "memory": {
            "short_term_hits": result["short_term_memory_hits"],
            "long_term_hits": result["long_term_memory_hits"],
        },
        "context": {
            "sections": result["context_sections"],
            "assembled_context": result["assembled_context"],
        },
        "trace": {
            "trace_id": result["trace_id"],
            "applied_policy": result["applied_policy"],
            "applied_scope": payload.scope.model_dump(),
        },
    }
    if result.get("debug") is not None:
        response["debug"] = result["debug"]
    return response


@router.post("/retrieval/search", response_model=KnowledgeSearchResponse)
def retrieval_search(
    payload: KnowledgeSearchRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.search_knowledge(
            db=db,
            wallet_address=wallet_address,
            kb_ids=payload.kb_ids,
            query=payload.query,
            top_k=payload.top_k,
            filters=payload.filters.model_dump(),
            source_scope=payload.source_scope,
            debug=payload.debug,
            request_context={},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/retrieval/retrieve", response_model=KnowledgeRetrieveResponse)
def retrieval_retrieve(
    payload: KnowledgeRetrieveRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.retrieve_knowledge(
            db=db,
            wallet_address=wallet_address,
            kb_ids=payload.kb_ids,
            query=payload.query,
            top_k=payload.top_k,
            filters=payload.filters.model_dump(),
            source_scope=payload.source_scope,
            retrieval_policy=payload.retrieval_policy.model_dump(),
            debug=payload.debug,
            request_context={},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/retrieval/recall-memory", response_model=MemoryRecallResponse)
def retrieval_recall_memory(
    payload: RecallMemoryRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.recall_memory(
            db=db,
            wallet_address=wallet_address,
            query=payload.query,
            session_id=payload.session_id,
            memory_namespace=payload.memory_namespace,
            kb_ids=payload.kb_ids,
            retrieval_policy=payload.retrieval_policy.model_dump(),
            debug=payload.debug,
            request_context={"session_id": payload.session_id, "memory_namespace": payload.memory_namespace},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/retrieval/assemble-context", response_model=ContextAssembleResponse)
def retrieval_assemble_context(
    payload: ContextAssembleRequest,
    wallet_address: str = Depends(get_current_wallet),
):
    _ = wallet_address
    raw = payload.model_dump()
    return service.assemble_context(
        query=payload.query,
        knowledge_hits=raw["knowledge_hits"],
        short_term_hits=raw["short_term_hits"],
        long_term_hits=raw["long_term_hits"],
        retrieval_policy=payload.retrieval_policy.model_dump(),
        request_context=payload.request_context,
        debug=payload.debug,
    )


@router.post("/retrieval/generate-context", response_model=BotRetrievalContextResponse)
def retrieval_generate_context(
    payload: BotRetrievalContextRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.generate_context(
            db=db,
            wallet_address=wallet_address,
            kb_ids=payload.kb_ids,
            query=payload.query,
            session_id=payload.session_id,
            memory_namespace=payload.memory_namespace,
            top_k=payload.top_k,
            filters=payload.filters.model_dump(),
            source_scope=payload.source_scope,
            retrieval_policy={
                **payload.retrieval_policy.model_dump(),
                "token_budget": payload.token_budget or payload.retrieval_policy.token_budget,
                "top_k": payload.top_k or payload.retrieval_policy.top_k,
            },
            request_context=_bot_request_context(payload),
            debug=payload.debug,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/bot/retrieval-context", response_model=BotRetrievalContextResponse)
def bot_retrieval_context(
    payload: BotRetrievalContextRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.generate_context(
            db=db,
            wallet_address=wallet_address,
            kb_ids=payload.kb_ids,
            query=payload.query,
            session_id=payload.session_id,
            memory_namespace=payload.memory_namespace,
            top_k=payload.top_k,
            filters=payload.filters.model_dump(),
            source_scope=payload.source_scope,
            retrieval_policy={
                **payload.retrieval_policy.model_dump(),
                "token_budget": payload.token_budget or payload.retrieval_policy.token_budget,
                "top_k": payload.top_k or payload.retrieval_policy.top_k,
            },
            request_context=_bot_request_context(payload),
            debug=payload.debug,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/retrieval/context", response_model=RetrievalContextResponse, response_model_exclude_none=True)
def retrieval_context(
    payload: RetrievalContextRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        result = service.generate_context(
            db=db,
            wallet_address=wallet_address,
            kb_ids=payload.scope.kb_ids,
            query=payload.query,
            session_id=payload.conversation.session_id,
            memory_namespace=payload.conversation.memory_namespace,
            top_k=payload.policy.top_k,
            filters=payload.scope.filters.model_dump(),
            source_scope=payload.scope.source_scope,
            retrieval_policy=payload.policy.model_dump(),
            request_context={
                "conversation": payload.conversation.model_dump(exclude_none=True),
                "caller": payload.caller.model_dump(exclude_none=True),
            },
            debug=payload.debug,
        )
        return _build_neutral_context_response(payload, result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
