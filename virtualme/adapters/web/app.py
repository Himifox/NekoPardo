"""FastAPI assembly. No QQ plugins are imported here."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from start import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from virtualme.runtime import shutdown
from virtualme.adapters.web.routes import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await shutdown()

def create_app() -> FastAPI:
    application = FastAPI(title="猫娘管理控制台", version="0.1.0", lifespan=lifespan)
    application.include_router(router)
    return application

app = create_app()
