"""SASS backend API (FastAPI).

The frontend talks only to this service. It reads classrooms and findings from the shared
Postgres and calls the agentic service (AgenticAI/) to start reviews.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import classrooms, findings, health, reviews


def create_app() -> FastAPI:
    app = FastAPI(title="SASS Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(classrooms.router)
    app.include_router(findings.router)
    app.include_router(reviews.router)
    return app


app = create_app()
