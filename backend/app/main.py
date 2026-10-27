"""
FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import CORS_ORIGINS, DATA_DIR, PROCESSED_DIR
from app.core.logging_config import configure_logging

configure_logging("INFO")
logger = logging.getLogger(__name__)


def validate_environment() -> None:
    """Validate critical dependencies and data files on startup."""
    # 1. Validate Parquet engine
    engine_found = False
    try:
        import pyarrow
        logger.info("Parquet engine 'pyarrow' is available (version %s).", pyarrow.__version__)
        engine_found = True
    except ImportError:
        try:
            import fastparquet
            logger.info("Parquet engine 'fastparquet' is available.")
            engine_found = True
        except ImportError:
            pass

    if not engine_found:
        logger.critical(
            "CRITICAL: Neither 'pyarrow' nor 'fastparquet' is installed. "
            "Parquet data files cannot be loaded. "
            "Install pyarrow: pip install pyarrow>=14.0.0"
        )

    # 2. Validate data paths
    actuals_path = PROCESSED_DIR / "actuals.parquet"
    forecasts_path = PROCESSED_DIR / "forecasts.parquet"
    logger.info("Data directory configured at: %s", DATA_DIR)

    if actuals_path.is_file():
        logger.info("Found actuals Parquet: %s (%.1f KB)", actuals_path, actuals_path.stat().st_size / 1024)
    else:
        logger.warning("Actuals Parquet NOT found at %s", actuals_path)

    if forecasts_path.is_file():
        logger.info("Found forecasts Parquet: %s (%.1f KB)", forecasts_path, forecasts_path.stat().st_size / 1024)
    else:
        logger.warning("Forecasts Parquet NOT found at %s", forecasts_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wind Forecast Intelligence Portal — starting up.")
    validate_environment()
    # Pre-load cache on startup
    try:
        from app.core.cache import get_cache
        get_cache()
    except Exception as exc:
        logger.critical("Startup cache pre-load failed: %s", exc, exc_info=True)
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

cors_origins = [o.strip() for o in CORS_ORIGINS if o.strip()]
allow_all = "*" in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all else cors_origins,
    allow_credentials=False if allow_all else True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Ensure all unhandled exceptions return JSON with CORS headers."""
    logger.exception("Unhandled error processing %s %s: %s", request.method, request.url.path, exc)
    origin = request.headers.get("origin")
    response = JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        },
    )
    if allow_all:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response

app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
def root():
    return {
        "product": "Wind Forecast Intelligence Portal",
        "docs": "/docs",
        "health": "/api/health",
    }
