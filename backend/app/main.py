from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.flex.scheduler import shutdown_scheduler, start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Returns None when disabled or when Flex credentials are absent, so an
    # upload-only deployment simply has no scheduler rather than a job that
    # fails every morning (§3.8).
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    lifespan=lifespan,
    title="Settled — Trade Performance Tracker",
    version="0.1.0",
    description=(
        "Performance attribution for a personal IBKR account. "
        "Not a tax record: FIFO matching serves performance attribution only, "
        "with no wash-sale or tax-lot handling (§1.3)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
