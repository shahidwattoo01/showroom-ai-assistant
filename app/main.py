import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import admin, webchat
from .agents import get_engine
from .channels import voice, whatsapp
from .config import BASE_DIR, settings
from .db import init_db
from .knowledge import kb
from .scheduler import scheduler_loop
from .seed import run_seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_seed()
    kb.rebuild()
    get_engine()
    task = asyncio.create_task(scheduler_loop()) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()


app = FastAPI(title=f"{settings.showroom_name} AI Customer Service", lifespan=lifespan)
app.include_router(webchat.router)
app.include_router(admin.router)
app.include_router(whatsapp.router)
app.include_router(voice.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "chat.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/widget.js", include_in_schema=False)
def widget():
    return RedirectResponse("/static/widget.js")


@app.get("/health")
def health():
    return {"ok": True, "engine": get_engine().name, "whatsapp": settings.whatsapp_enabled,
            "database": "sqlite" if settings.is_sqlite else "postgresql"}
