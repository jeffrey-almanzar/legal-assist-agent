# Entry point of the FastAPI application; defines the routes and main logic.

# main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.routers import feedback, legal_queries

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()

app = FastAPI(lifespan=lifespan)

@app.get('/')
async def root():
    return {'message': 'Welcome to the Legal Assistance API'}

app.include_router(legal_queries.router)
app.include_router(feedback.router)
