"""Vercel entry point: Vercel serves every request through this function
(see vercel.json), which is just the FastAPI app from app/main.py."""

from app.main import app  # noqa: F401
