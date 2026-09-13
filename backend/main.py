from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root and backend root are on PYTHONPATH
BACKEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_ROOT.parent

for p in (PROJECT_ROOT, BACKEND_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from backend.api.routes import router as api_router
    from backend.services.job_manager import job_manager
except ImportError:
    from api.routes import router as api_router
    from services.job_manager import job_manager


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run service startup work using FastAPI's lifespan interface."""
    # Keep recoverable jobs for a week so a restart does not discard results.
    job_manager.cleanup_old_jobs(max_age_seconds=7 * 24 * 60 * 60)
    yield


app = FastAPI(
    title="PDF to LaTeX Conversion Service",
    description="Production-ready PDF to LaTeX conversion with GPU-accelerated OCR and chapter splitting.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS support for local React frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def root():
    return {
        "name": "PDF to LaTeX Service",
        "docs": "/docs",
        "health": "/api/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
