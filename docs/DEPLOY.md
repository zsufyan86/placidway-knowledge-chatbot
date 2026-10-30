# Deploy to Hugging Face Spaces (free)

Time needed: about 20 minutes.

## 1. Test locally first

Follow "Run it locally" in the README: `pytest -q` (all pass), start `uvicorn app.main:app --port 7860`, ask the 8 test questions in the browser, then run `python scripts/eval.py` and read `test_results.md`.

## 2. Knowledge base snapshot

`data/kb.json` is already in the repo (7 pages, 127 chunks), so the live bot works instantly and never depends on PlacidWay accepting requests from Hugging Face's servers. To refresh it before deploying:

```bash
python -m app.ingest
```

You should see 7 lines, one per page, roughly 120 to 140 chunks in total, and "Saved data/kb.json". If a page shows ERROR, the old file is kept; run it again later.

## 3. Push the code to GitHub

```bash
git init
git add .
git commit -m "PlacidWay knowledge chatbot"
git branch -M main
git remote add origin https://github.com/zsufyan86/placidway-knowledge-chatbot.git
git push -u origin main
```

Create the empty repo on GitHub first (public). Check that `.env` is NOT in the repo (`git status` must not list it; it is in `.gitignore`).

## 4. Create the Space

1. huggingface.co → New → Space.
2. Name: `placidway-chatbot`. SDK: **Docker**, template **Blank**. Hardware: **CPU basic (free)**. Visibility: **Public**.
3. Settings → **Variables and secrets** → New secret:
   - `GEMINI_API_KEY` = your key from aistudio.google.com/apikey
   - `ADMIN_TOKEN` = a long random string (for refresh and admin exports)

## 5. Upload the code to the Space

Easiest: in the Space, **Files** → **Add file** → **Upload files**, and drag in everything from the project folder (including `data/kb.json`, excluding `.env`, `.venv`, `logs`). Commit.

Or with git:

```bash
git remote add space https://huggingface.co/spaces/<your-hf-username>/placidway-chatbot
git push space main
```

(git asks for a password: use a Hugging Face access token with write permission.)

The README's top block (`sdk: docker`, `app_port: 7860`) tells Hugging Face how to run it. The build takes 2 to 4 minutes; watch the **Logs** tab.

## 6. Check it

- Open `https://<your-hf-username>-placidway-chatbot.hf.space` (the direct URL, without the Hugging Face frame, is the one to submit).
- `.../api/health` should show `"ok": true`, `"llm_configured": true` and, after the first question, the model name.
- Ask the 8 test questions. Then run the eval from your machine:

```bash
python scripts/eval.py https://<your-hf-username>-placidway-chatbot.hf.space
```

Commit the generated `test_results.md` to GitHub and the Space, and paste the live URL into the top of `README.md`. Both are required deliverables.

## 7. Refresh content on the live bot

```bash
curl -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" https://<space-url>/api/refresh
# about 70 s later:
curl https://<space-url>/api/kb
```

## 8. Keep it alive for 7 days

Free Spaces sleep after a period with no visits and wake on the next visit (slower first answer). Open the bot once a day during the review week. Don't push changes during the review window unless something is broken.

## Read the logs

```bash
curl -H "Authorization: Bearer <ADMIN_TOKEN>" https://<space-url>/api/admin/gaps
curl -H "Authorization: Bearer <ADMIN_TOKEN>" https://<space-url>/api/admin/leads
```
