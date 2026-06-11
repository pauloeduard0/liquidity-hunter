"""FastAPI application entrypoint for the `api` layer.

Run locally with:

    poetry run uvicorn liquidity_hunter.api.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from liquidity_hunter.api.routes import dashboard, health

app = FastAPI(title="Liquidity Hunter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(dashboard.router)
