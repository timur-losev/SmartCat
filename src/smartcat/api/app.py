"""FastAPI application for SmartCat web interface."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from smartcat.api import deps
from smartcat.api.routes_chat import router as chat_router
from smartcat.api.routes_openai import router as openai_router
from smartcat.api.routes_agora import router as agora_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: pre-load agent (embedder + reranker warm-up)
    await deps.get_agent()
    yield
    # Shutdown
    await deps.shutdown()


app = FastAPI(title="SmartCat", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(chat_router, prefix="/api")
app.include_router(openai_router)  # /v1/chat/completions (no prefix)
app.include_router(agora_router, prefix="/api/agora")

# Static frontend
web_dir = Path(__file__).resolve().parent.parent.parent.parent / "web"
if web_dir.exists():
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
