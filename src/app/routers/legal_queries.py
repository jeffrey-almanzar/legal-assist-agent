# Defines the routes for handling legal queries, integrating with Claude.

# legal_queries.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import rate_limit
from app.auth import Caller, get_caller
from app.services import QueryRefusedError, process_query

router = APIRouter()

DISCLAIMER = 'This is general legal information, not legal advice.'


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)


class Reference(BaseModel):
    question: str
    sources: list[str]


class QueryResponse(BaseModel):
    # Pass this to POST /feedback to rate the answer.
    query_id: UUID
    response: str
    # Firm-approved knowledge base entries the answer was based on.
    references: list[Reference] = []
    disclaimer: str = DISCLAIMER


@router.post('/query', response_model=QueryResponse)
async def handle_query(request: QueryRequest, caller: Caller = Depends(get_caller)):
    await rate_limit.enforce(caller, 'query')
    try:
        result = await process_query(request.query)
    except QueryRefusedError:
        raise HTTPException(status_code=422, detail='This query could not be answered.')
    return QueryResponse(
        query_id=result.query_id,
        response=result.response,
        references=[Reference(question=r.question, sources=r.sources) for r in result.references],
    )
