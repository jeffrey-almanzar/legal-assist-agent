# Entry point of the FastAPI application; defines the routes and main logic.

# main.py
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import db, services
from app.routers import feedback, legal_queries

STATIC_DIR = Path(__file__).parent / 'static'

@asynccontextmanager
async def lifespan(app: FastAPI):
    if services.MOCK_LLM:
        logging.getLogger('uvicorn.error').warning('MOCK_LLM=1: answers are canned, Claude is not called.')
    await db.connect()
    yield
    await db.close()

app = FastAPI(lifespan=lifespan)

@app.get('/', include_in_schema=False)
async def chat_page():
    return FileResponse(STATIC_DIR / 'index.html')

@app.get('/health')
async def health():
    return {'status': 'ok', 'mock_llm': True} if services.MOCK_LLM else {'status': 'ok'}

app.include_router(legal_queries.router)
app.include_router(feedback.router)
