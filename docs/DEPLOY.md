# Deploy to Vercel (free)

Live: https://placidway-chatbot-five.vercel.app

Hugging Face Docker Spaces now need a paid plan, so the bot runs on Vercel's free Hobby plan. Vercel detects the FastAPI app in `app/main.py` and runs it as a serverless Python function. No code or build settings are needed beyond `.vercelignore`.

## 1. Test locally first

Follow "Run it locally" in the README: `pytest -q` (all pass), start `uvicorn app.main:app --port 7860`, try the questions in the browser, then `python scripts/eval.py`.

## 2. Knowledge base snapshot

`data/kb.json` is in the repo (7 pages, 127 chunks), so the live bot answers from the first request and never needs PlacidWay to accept requests from Vercel's servers. To update it: `python -m app.ingest`, check the output (7 pages, no ERROR lines), commit, redeploy.

## 3. Deploy

Needs Node.js (for `npx`) and a free Vercel account.

```bash
npx vercel login
npx vercel link --yes --project placidway-chatbot
npx vercel env add GEMINI_API_KEY production     # paste the key when asked
npx vercel env add ADMIN_TOKEN production        # any long random string
npx vercel deploy --prod
```

The last command prints the production URL. Secrets live only in Vercel's encrypted environment variables; `.env` is listed in `.vercelignore` and `.gitignore`, so it is never uploaded.

## 4. Check it

- `https://<url>/api/health` shows `"ok": true`, `"chunks": 127` and `"llm_configured": true`.
- Run the eval against it and commit the result:

```bash
python scripts/eval.py https://placidway-chatbot-five.vercel.app
```

## 5. Refresh content

```bash
curl -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" https://<url>/api/refresh
```

On Vercel this crawls the 7 pages right away (about 70 s, because robots.txt asks for 10 s between requests) and returns which pages changed. It only updates the running instance, since the function's files are read-only. To make a refresh permanent, run `python -m app.ingest` locally and redeploy.

## 6. Logs

```bash
curl -H "Authorization: Bearer <ADMIN_TOKEN>" https://<url>/api/admin/gaps
curl -H "Authorization: Bearer <ADMIN_TOKEN>" https://<url>/api/admin/leads
```

They are kept in `/tmp` and reset when the function cold-starts. `vercel logs <url>` shows the server log, which never contains chat text or lead details.

## Other hosts

The `Dockerfile` runs the same app on any container host (Render, Fly.io, Railway, Hugging Face PRO, a VM): it listens on `$PORT` (default 7860) and needs the same two environment variables.
