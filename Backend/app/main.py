"""SASS backend API (FastAPI).

The frontend talks only to this service. It reads classrooms and findings from the shared
Postgres and calls the agentic service (AgenticAI/) to start reviews.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import DatabaseUnavailable
from app.routers import auth, classrooms, findings, health, reviews


def create_app() -> FastAPI:
    app = FastAPI(title="SASS Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.exception_handler(DatabaseUnavailable)
    def database_unavailable(_request: Request, exc: DatabaseUnavailable) -> JSONResponse:
        # A clear, retryable 503 instead of a bare "500 Internal Server Error".
        return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "5"})

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(classrooms.router)
    app.include_router(findings.router)
    app.include_router(reviews.router)
    return app


app = create_app()
