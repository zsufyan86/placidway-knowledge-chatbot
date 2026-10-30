# Write-up

## What I'd improve with another week

1. **A bigger evaluation set, scored automatically.** `scripts/eval.py` runs 21 cases (the brief's 8 plus 13 edge cases) with simple checks. I'd grow it to 60 to 80 questions, including every price on every page, questions that must be refused, and adversarial ones, and run it on every change. A perfect score on a small set can hide gaps.
2. **Structured price records.** Prices are checked in code today (each one must be on a cited page and next to the right treatment, clinic or country), but they still pass through the model's wording. Next step: extract a `treatment | country | program | price | currency | context` table at ingest time and answer plain cost questions straight from it, with the model only phrasing the result.
3. **Per-sentence citations.** The model cites sources for the whole answer. Asking for a source number after each sentence would let the app check every claim, not only the prices.
4. **Hybrid retrieval.** Keep BM25 for exact terms and add multilingual embeddings (run locally, no API cost) so paraphrased and non-English questions don't depend on the rewrite call.
5. **Change alerts on refresh.** Refresh already reports which pages changed. Next: a weekly scheduled refresh, a diff of prices before and after, and an alert if extraction suddenly returns much less text (a sign the page layout changed).
6. **Durable storage.** Logs and leads are JSONL files in `/tmp` on the serverless function, which Vercel doesn't keep across cold starts. I'd send them to a small database or straight into PlacidWay's CRM, with a simple dashboard of content gaps.

## Known limitations

- The bot only knows the 7 pages. Other treatments, countries and clinics get an honest "not on these pages" plus the quote link. That's by design.
- BM25 matches words, not meaning. A question that shares no words with the pages can miss; the rewrite step reduces this but costs an extra call.
- The price-pairing check is a rule of thumb, not a proof. It catches a price moved to another page, country or cancer type, which is how the model most often goes wrong when it sees several pages at once. It is lenient within one page (it accepts any specific word from the same page), so it is a strong safety net, not a guarantee. Answers in languages other than English get the "price is on a cited page" check but the pairing check mostly skips them, because their words aren't in the English pages.
- Non-numeric claims in an answer are covered by the prompt, the citation requirement and the short limit on uncited replies, but not checked sentence by sentence (see improvement 3). In testing, the model sometimes adds small unsourced wording (for example "for men and women" in an Arabic answer). For that reason, replies to booking, quote, medical and out-of-scope questions are written by the app, not the model.
- Gemini's free tier has daily limits. When it's exhausted the bot says so politely and offers the quote form instead of failing.
- On Vercel's free plan the function cold-starts after a quiet period, so the first answer can take a few seconds longer. Logs and leads live in `/tmp` and are lost on a cold start (see improvement 6). A refresh through `/api/refresh` updates the running instance only; to make it permanent, run `python -m app.ingest` and redeploy.
- The per-IP rate limit trusts `X-Forwarded-For`, which can be faked; a global cap for all visitors backs it up.
