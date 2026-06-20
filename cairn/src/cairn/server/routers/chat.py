from __future__ import annotations

from fastapi import APIRouter

from cairn.server import chat
from cairn.server.models import ChatTurnRequest, ChatTurnResult, ChatWorker

router = APIRouter(tags=["chat"])


@router.get("/chat/workers", response_model=list[ChatWorker])
def get_chat_workers():
    return chat.list_workers()


@router.post("/chat/turn", response_model=ChatTurnResult)
def post_chat_turn(body: ChatTurnRequest):
    return chat.run_turn(body.worker, body.message, body.session, body.debug)


@router.get("/chat/context")
def get_chat_context():
    return {"files": chat.worker_context_files()}
