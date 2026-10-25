"""
FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import CORS_ORIGINS
from app.core.logging_config import configure_logging

configure_logging("INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wind Forecast Intelligence Portal — starting up.")
    # Pre-load cache on startup (non-blocking for missing data)
    try:
        from app.core.cache import get_cache
        get_cache()
    except Exception as exc:
        logger.warning("Cache pre-load failed (will retry on first request): %s", exc)
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Wind Forecast Intelligence Portal",
    description=(
        "Day-ahead wind forecast monitoring and accuracy analytics. "
        "Powered by Elexon BMRS data (FUELHH actuals + WINDFOR forecasts)."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
def root():
    return {
        "product": "Wind Forecast Intelligence Portal",
        "docs": "/docs",
        "health": "/api/health",
    }
