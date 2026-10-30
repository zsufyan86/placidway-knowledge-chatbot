# PlacidWay Website Knowledge Chatbot

A chatbot that answers visitor questions using **only** the 7 PlacidWay pages from the brief, links every answer to the page it came from, and says "I don't know" (with a free quote link) instead of inventing prices, clinics or medical facts.

- **Live bot:** https://placidway-chatbot-five.vercel.app (public, no login)
- **Code:** https://github.com/zsufyan86/placidway-knowledge-chatbot
- **Test results:** [`test_results.md`](test_results.md): the 8 brief questions + 13 edge cases, run against the live URL (21/21 automatic checks pass; read the answers too)
- **Knowledge base snapshot:** [`data/kb.json`](data/kb.json), 7 pages / 127 chunks, crawled 2026-10-29
- **Write-up:** [`WRITEUP.md`](WRITEUP.md) (limitations and what I'd do with another week)
- **Stack:** Python 3.11, FastAPI, BeautifulSoup, BM25 (`rank-bm25`), Google Gemini (free tier), plain HTML/JS chat UI, deployed on Vercel (free plan); a Dockerfile is included for any container host

## What it does

| Requirement | How it's met |
|---|---|
| Content ingestion | `app/crawler.py` fetches only the 7 URLs in `app/sources.py`. It reads robots.txt, skips anything disallowed, honours `Crawl-delay` (PlacidWay sets 10 s, so a crawl takes about 70 s, slower than the brief's 1 req/s on purpose) and sends a named User-Agent. |
| Knowledge base | `app/extract.py` keeps the three regions that hold real content: the title/price header, the article, and the sidebar facts. Navigation, footer, popups, forms and "related" carousels are removed. `app/chunker.py` makes one chunk per H2 section (FAQs stay with their answers), plus an overview chunk per page. Stored in `data/kb.json`. |
| Finding the right content | `app/retrieve.py`: BM25 keyword search with a small synonym map, max 4 chunks per page so one long page can't crowd out the rest, a boost for chunks that contain a price when the question is about cost, and the overview chunk of each page attached (that's where the headline price lives). |
| Grounded answers + citations | Gemini only sees the retrieved chunks, numbered as sources, and must return JSON with the source numbers it used. The **server** builds the source links from those numbers, so the model can never invent a URL. Every figure in the answer must appear in a source it **cited** (not just somewhere in what was retrieved). |
| Honest "I don't know" | Prompt rules plus code checks. Unknowns get a clear "not on these pages" and a free quote link, and are logged as content gaps. A reply without sources must be short and may not contain any price. "I can't book", "here's the quote form", "ask your doctor" and "out of scope" replies are fixed text written by the app (English, Spanish, Arabic), so the model can't add facts there. |
| Cost questions | Prices are copied with currency and context (starting from, out-patient vs in-patient, 3 weeks, what's included, optional extras). Code checks that each price is on a cited page **and sits next to the right treatment, clinic or country** (so "$64 for adenocarcinoma" or "Turkey $64" is rejected when $64 is a Thailand clinic). A price the user states ("I heard it's $50") is checked against the pages for that treatment before the model answers. |
| Chat UI | `static/index.html`: suggestions, typing indicator, source chips, one-time medical note, inline quote form, works on mobile, right-to-left text handled. |
| Refresh | `python -m app.ingest` locally, or `POST /api/refresh` on the live bot. A refresh only replaces the knowledge base if every page came back, and reports which pages changed. |
| Live deployment | Vercel (free Hobby plan, serverless Python). Vercel detects the FastAPI app in `app/main.py`; secrets are Vercel environment variables. A `Dockerfile` is also included. Steps in [`docs/DEPLOY.md`](docs/DEPLOY.md). |

**Nice to have, all done:** answers in the user's language (Spanish, Arabic, ...; fixed replies and fallbacks are translated to Spanish and Arabic too), follow-up questions using chat history, a medical disclaimer shown once per conversation, lead capture (name + email + consent), logging of unanswered questions (`GET /api/admin/gaps`). Optional voice agent: [`docs/VOICE.md`](docs/VOICE.md).

## How a question is answered

```
question
  │ 1. injection check (code)  ─────────────────────────────► polite refusal, no model call
  │ 2. BM25 on the question + the previous user question (handles "what about recovery?")
  │ 3. only if the question is in another language or nothing matched:
  │    one Gemini call rewrites it into an English search query, then search again
  │ 4. wrong-premise check (code): does a price the user states appear on the pages
  │    for the treatment they named? If not, the model is told so before answering
  │ 5. Gemini answers from the numbered chunks only → JSON {answer, citations, status, ...}
  │ 6. code checks ───── fail ──► retry once, telling the model what failed ──► safe fallback
  │      • answered but cited nothing? uncited reply longer than a short "not on these pages"?
  │      • any price / 3+ digit number not in the CITED sources or the user's words?
  │      • a price next to a treatment / clinic / country it doesn't belong to?
  │      • opens with "yes" to a figure the pages don't support?
  │      • a "yes, it's safe for you" style medical verdict?
  │      • prompt or secret leaked?
  │ 7. booking, quote, medical and out-of-scope replies: fixed text from the app, in the user's language
  │ 8. strip any URL that isn't a cited page or PlacidWay's quote/contact page
  ▼
answer + source links (built by the server) + quote link when useful + disclaimer once
every turn → logs/chats.jsonl, unanswered → logs/gaps.jsonl, quote requests → logs/leads.jsonl
```

Statuses the bot returns: `answered`, `not_in_content`, `cannot_do` (booking etc.), `quote_request` (opens the lead form), `medical_advice`, `out_of_scope`, `unavailable` (model quota on every model), `rate_limited`.

## Tools and models, and why

- **Gemini 3.5 Flash-Lite (free tier).** Free, fast (about 2 to 4 s per answer here), good at following a strict JSON format, and its free daily quota is far bigger than Flash. `GEMINI_MODEL` takes a list: the app uses the first model that exists for the key (Google retires free models often; `gemini-2.5-flash-lite` already returns 404 for new keys), and moves to the next one when a model hits its free-tier quota.
- **One LLM call per question in the normal case.** The rewrite call only happens for other languages or zero matches. That keeps the free quota for reviewers.
- **BM25 instead of embeddings.** About 130 chunks, and the words that matter are exact (adrenal, anal, adenocarcinoma, in-patient, Thailand, Turkey). No vector database, no second model, no extra API calls. Multilingual questions are handled by the rewrite step instead.
- **Rules in code, not just in the prompt.** A prompt can be talked around. The price check, citation check and link scrub can't.
- **How the price-pairing check works** (`guards.misattributed_figures`): for each clause with a price, find the source lines holding that price, with their heading and page title. If the clause names a country or cancer type and those lines name a different one, it fails. Otherwise the clause's *specific* words (words found in under 30% of chunks, minus generic ones like "cost" or "treatment", e.g. adrenal, ozone, Konya) must appear on that line or that page. Tested against the live pages with correct and deliberately wrong answers.
- **FastAPI + one HTML file.** Easy to read, easy to change live in the review call, no build step.
- **Rejected:** stuffing all 7 pages into every prompt (nothing to retrieve or tune, and it burns the free quota), letting the model write source URLs (it can invent them), a hosted chatbot builder (the grounding logic would be hidden).

## Run it locally

Use **Python 3.11 or 3.12** (the Docker image uses 3.11). On 3.13+/3.14 some pinned packages have no ready-made wheels yet and pip tries to compile them.

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env          # then open .env and paste your GEMINI_API_KEY and an ADMIN_TOKEN
pytest -q                       # 57 unit tests, no network or API key needed
uvicorn app.main:app --port 7860
```

macOS / Linux:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then add your GEMINI_API_KEY and an ADMIN_TOKEN
pytest -q
uvicorn app.main:app --port 7860
```

Open http://localhost:7860 and ask questions. `.env` is read automatically (`app/config.py`), no need to export variables. Then, in a second terminal with the venv active:

```bash
python scripts/eval.py          # the 8 test questions + 13 edge cases → test_results.md (about 3 minutes)
```

`data/kb.json` is already included, so the bot starts instantly. To re-crawl the 7 pages (about 70 s, robots.txt asks for 10 s between requests): `python -m app.ingest`. `python -m app.ingest --show 2` prints every chunk of page 2, handy for checking extraction. `GET /api/health` shows `llm_configured: false` if the key is missing.

## API

| Endpoint | Auth | What it does |
|---|---|---|
| `GET /` | public | Chat UI |
| `POST /api/chat` | public, rate-limited | `{message, history, disclaimer_shown}` → answer, sources, status, quote link |
| `POST /api/lead` | public, rate-limited | Stores name, email, question (consent required) |
| `GET /api/kb` | public | Pages, chunk counts, when they were fetched, last refresh result |
| `GET /api/kb/chunks?page=N` | public | The actual chunks, to inspect extraction |
| `GET /api/health` | public | Ready flag and the model in use |
| `POST /api/refresh` | `Bearer ADMIN_TOKEN` | Re-crawls the pages in the background |
| `GET /api/admin/{chats,gaps,leads}` | `Bearer ADMIN_TOKEN` | Logged turns, unanswered questions, quote requests |
| `POST /api/vapi/tool` | `x-vapi-secret` (off until `VAPI_TOOL_SECRET` is set) | Voice agent tool: same grounded pipeline, rate-limited |

## Project layout

```
app/
  sources.py    the 7 URLs and the quote/contact links
  crawler.py    robots.txt, Crawl-delay, User-Agent
  extract.py    HTML → title, overview facts, sections (tables as "Label: value")
  chunker.py    sections → chunks
  kb.py         build / save / load / safe refresh
  retrieve.py   BM25 search
  prompts.py    system prompt and JSON schemas
  llm.py        Gemini wrapper (model fallback, quota errors) + FakeLLM for tests
  guards.py     the code checks
  pipeline.py   retrieve → rewrite if needed → answer → verify
  store.py      JSONL logs and the rate limiter
  main.py       FastAPI routes
static/index.html   chat UI
scripts/eval.py     test questions against a running bot
data/kb.json        knowledge base snapshot (7 pages, 127 chunks)
tests/              unit tests with HTML fixtures and a fake LLM;
                    test_grounding.py rebuilds the live-page traps ($50 fee, $64 clinic)
```

## Security and privacy

No secrets in the code: the Gemini key and admin token come from environment variables (encrypted Vercel environment variables, or `.env` locally, which is git-ignored). Logs store questions for content-gap analysis; server logs never print chat text or lead details. Leads require a consent tick.

- **Rate limits:** per IP (40 per 10 minutes, enough for a reviewer running a long list of questions) plus a global cap for all visitors together (200 per 10 minutes). The per-IP key comes from `X-Forwarded-For`, which a client can fake, so the global cap is what really protects the free Gemini quota.
- **Admin and voice secrets** are compared in constant time. The voice tool is disabled unless `VAPI_TOOL_SECRET` is set.
- **Chat history comes from the browser**, so turns that look like prompt injection (including faked "assistant" turns) are dropped before they reach the model.
