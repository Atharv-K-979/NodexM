"""FastAPI application: API routes, background runner, and the built frontend."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import __version__
from .config import ROOT, settings
from .db import init_db
from .jobs import runner
from .routes import auth, runs, settings as settings_routes, threads, watches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("nodexm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not settings.gemini_api_key:
        log.warning("GEMINI_API_KEY is not set: only users with their own key can run research.")
    if not settings.tavily_api_key and not settings.searxng_url:
        log.warning("No TAVILY_API_KEY or SEARXNG_URL: web search is off, free source APIs still work.")
    await runner.start()
    yield
    await runner.stop()


app = FastAPI(title="NodexM API", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
for r in (auth.router, settings_routes.router, threads.router, runs.router, watches.router):
    app.include_router(r)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


# Serve the built frontend (npm run build) from the same process when it exists.
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = (DIST / path).resolve()
        if path and file.is_file() and DIST.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
