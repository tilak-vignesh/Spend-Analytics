from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Before importing app modules: app.db reads TXN_DB_PATH at import time.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from app.db import DB_PATH, REPO_ROOT  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.routers import anomalies, chat, dashboard, statements, transactions  # noqa: E402

FRONTEND = REPO_ROOT / "frontend" / "index.html"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate(DB_PATH)
    yield


app = FastAPI(title="Personal Transaction Agent", lifespan=lifespan)
for module in (dashboard, transactions, anomalies, statements, chat):
    app.include_router(module.router)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND, media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
