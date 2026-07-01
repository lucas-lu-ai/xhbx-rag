from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="xhbx-rag Web")


app = create_app()
