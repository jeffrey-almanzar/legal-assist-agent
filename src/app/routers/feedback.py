# Defines the route for rating answers, so attorneys can flag bad ones and promote good ones.

# feedback.py
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db, rate_limit
from app.auth import Caller, get_caller

router = APIRouter()


class FeedbackRequest(BaseModel):
    query_id: UUID
    rating: Literal['up', 'down']
    comment: str | None = Field(default=None, max_length=2_000)


class FeedbackResponse(BaseModel):
    feedback_id: int


@router.post('/feedback', response_model=FeedbackResponse, status_code=201)
async def handle_feedback(request: FeedbackRequest, caller: Caller = Depends(get_caller)):
    await rate_limit.enforce(caller, 'feedback')
    try:
        # Record who rated it; only attorney ratings become knowledge base candidates.
        feedback_id = await db.add_feedback(
            request.query_id, request.rating, request.comment,
            source=caller.role, api_key_id=caller.api_key_id,
        )
    except db.QueryNotFoundError:
        raise HTTPException(status_code=404, detail='Unknown query_id.')
    return FeedbackResponse(feedback_id=feedback_id)
