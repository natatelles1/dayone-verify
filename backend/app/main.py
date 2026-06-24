import logging
from contextlib import asynccontextmanager

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from sqlalchemy import text

from app.core import logging as app_logging
from app.core.config import settings
from app.core.db import engine

app_logging.setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"env": settings.app_env})
    yield
    logger.info("shutdown")


app = FastAPI(title="DayOne Verify API", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    result: dict[str, str] = {"app": "ok", "db": "error", "r2": "error"}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        result["db"] = "ok"
    except Exception as exc:
        logger.warning("db_health_failed", extra={"error": str(exc)})
        result["db"] = f"error: {type(exc).__name__}"

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
        )
        s3.head_bucket(Bucket=settings.r2_bucket)
        result["r2"] = "ok"
    except (BotoCoreError, ClientError) as exc:
        logger.warning("r2_health_failed", extra={"error": str(exc)})
        result["r2"] = f"error: {type(exc).__name__}"

    return result
