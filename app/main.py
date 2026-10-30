"""FastAPI app: chat API, lead capture, refresh, admin exports, voice tool."""

from __future__ import annotations

import hmac
import json
import logging
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, store
from .kb import KnowledgeBase
from .llm import GeminiLLM
from .pipeline import respond
from .sources import QUOTE_URL, SOURCES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

kb = KnowledgeBase()
llm = GeminiLLM()
limiter = store.RateLimiter(config.RATE_LIMIT_REQUESTS, config.RATE_LIMIT_WINDOW)
# One cap for everybody together. The per-IP limit relies on X-Forwarded-For,
# which a client can fake, so this is what really protects the Gemini quota.
global_limiter = store.RateLimiter(config.GLOBAL_RATE_LIMIT_REQUESTS, config.RATE_LIMIT_WINDOW)
STATIC = config.ROOT / "static"

BUSY = {"answer": "The assistant is very busy right now. Please wait a few minutes and try again, "
                  "or request a free quote and the PlacidWay team will reply directly.",
        "sources": [], "status": "rate_limited", "wants_quote": True, "quote_url": QUOTE_URL, "disclaimer": None}


# Load the committed snapshot as soon as the module is imported, so the bot is
# ready even on hosts that don't run ASGI startup events (serverless).
if config.KB_PATH.exists():
    kb.load_or_build()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not config.GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY is not set: the bot will answer every question with a 'try again' message")
    if not config.ADMIN_TOKEN:
        log.warning("ADMIN_TOKEN is not set: /api/refresh and the admin exports are disabled")
    # No snapshot (already loaded above if there is one): crawl in the
    # background so the server is reachable straight away.
    if not kb.ready:
        threading.Thread(target=kb.load_or_build, daemon=True).start()
    yield


app = FastAPI(title="PlacidWay Knowledge Chatbot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def client_ip(request: Request) -> str:
    # Behind Hugging Face's proxy the first X-Forwarded-For entry is the
    # visitor. It can be faked, which is why global_limiter exists as well.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def allowed(key: str) -> bool:
    return global_limiter.allow("all") and limiter.allow(key)


def _same(given: str | None, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest((given or "").encode(), expected.encode())


def require_admin(authorization: str | None) -> None:
    if not _same(authorization, f"Bearer {config.ADMIN_TOKEN}" if config.ADMIN_TOKEN else ""):
        raise HTTPException(status_code=401, detail="unauthorized")


class Turn(BaseModel):
    role: str
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=40)
    disclaimer_shown: bool = False


class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=200)
    question: str = Field(default="", max_length=1000)
    consent: bool = False


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    s = kb.stats()
    return {"ok": s["ready"], "kb_built_at": s["built_at"], "chunks": s["chunks"],
            "llm_configured": bool(config.GEMINI_API_KEY), "model": llm.active_model,
            "refreshing": s["refreshing"]}


@app.get("/api/config")
def public_config():
    return {"quote_url": QUOTE_URL, "vapi_public_key": config.VAPI_PUBLIC_KEY or None,
            "vapi_assistant_id": config.VAPI_ASSISTANT_ID or None,
            "sources": [s["url"] for s in SOURCES]}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    if not allowed(client_ip(request)):
        return JSONResponse(status_code=429, content=BUSY)
    return respond(body.message, [t.model_dump() for t in body.history], kb, llm,
                   disclaimer_shown=body.disclaimer_shown)


@app.post("/api/lead")
def lead(body: LeadIn, request: Request):
    if not allowed("lead:" + client_ip(request)):
        raise HTTPException(status_code=429, detail="too many requests")
    if not EMAIL.match(body.email.strip()):
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")
    if not body.consent:
        raise HTTPException(status_code=422, detail="Please tick the consent box so PlacidWay can contact you.")
    store.record("leads", {"name": body.name.strip(), "email": body.email.strip(),
                           "question": body.question.strip()})
    log.info("lead stored (email domain %s)", body.email.split("@")[-1])
    return {"ok": True, "message": "Thanks! The PlacidWay team will contact you with a free quote."}


@app.get("/api/kb")
def kb_overview():
    """Public: what the bot knows (pages, chunk counts, when it was fetched)."""
    return kb.stats()


@app.get("/api/kb/chunks")
def kb_chunks(page: int | None = None):
    chunks = (kb.kb or {}).get("chunks", [])
    if page is not None:
        chunks = [c for c in chunks if c["id"].startswith(f"p{page}-")]
    return chunks


@app.post("/api/refresh")
def refresh(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if kb.refreshing:
        return {"status": "already running"}
    if config.SERVERLESS:
        # A serverless function stops once it has replied, so crawl now
        # (about 70 s) and return the result.
        return kb.refresh()
    threading.Thread(target=kb.refresh, daemon=True).start()
    return {"status": "started", "note": "takes about 70 seconds (robots.txt Crawl-delay is 10s); "
                                         "check GET /api/kb for last_refresh"}


@app.get("/api/admin/{name}")
def admin_export(name: str, authorization: str | None = Header(default=None), limit: int = 500):
    require_admin(authorization)
    if name not in {"chats", "gaps", "leads"}:
        raise HTTPException(status_code=404, detail="unknown list")
    return store.read(name, limit)


# ---------- voice agent tool (Vapi) ----------

def _vapi_calls(payload: dict) -> list[tuple[str, dict]]:
    msg = payload.get("message", payload)
    calls = msg.get("toolCallList") or msg.get("toolCalls") or []
    out = []
    for c in calls:
        fn = c.get("function") or c
        args = fn.get("arguments") or c.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"question": args}
        out.append((c.get("id", ""), args))
    return out


@app.post("/api/vapi/tool")
async def vapi_tool(request: Request, x_vapi_secret: str | None = Header(default=None)):
    # Without a shared secret anyone could use this to spend the Gemini quota,
    # so the voice tool stays off until VAPI_TOOL_SECRET is set.
    if not config.VAPI_TOOL_SECRET:
        raise HTTPException(status_code=503, detail="voice tool is not configured")
    if not _same(x_vapi_secret, config.VAPI_TOOL_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = await request.json()
    results = []
    for call_id, args in _vapi_calls(payload)[:3]:
        if not allowed("vapi"):
            results.append({"toolCallId": call_id, "result": BUSY["answer"]})
            continue
        question = str(args.get("question", ""))[:800]
        r = respond(question, [], kb, llm, disclaimer_shown=True, channel="voice")
        spoken = r["answer"]
        if r["sources"]:
            spoken += " Source: the PlacidWay page " + r["sources"][0]["title"] + "."
        # Vapi requires a flat, single-line string; markdown bullets don't read well aloud.
        spoken = re.sub(r"\s+", " ", spoken.replace("**", "").replace("- ", "")).strip()
        results.append({"toolCallId": call_id, "result": spoken})
    return {"results": results}
