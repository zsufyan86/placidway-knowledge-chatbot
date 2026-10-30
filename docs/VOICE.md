# Optional: voice agent (Vapi)

The role is "AI Chatbot & Voice Agent Developer", so the bot can also take questions by voice. The voice agent does **not** answer from its own knowledge: it calls the same grounded pipeline through `POST /api/vapi/tool`, so voice answers pass the same price and citation checks as text.

Do this only after the text bot is deployed and passing the tests.

## 1. Create the tool

Vapi dashboard → **Tools** → Create tool → **Function** (custom tool).

- Name: `ask_placidway`
- Description: `Answers questions about ITC Immunity Therapy Center in Tijuana, its cancer treatment packages and prices, and alternative medicine prices abroad, using PlacidWay's pages only.`
- Parameters: one required string `question`, description `The caller's full question in English, rewritten to stand alone (include the treatment name if the caller said it earlier).`
- Server URL: `https://placidway-chatbot-five.vercel.app/api/vapi/tool`
- Required: set a secret header `x-vapi-secret` to a long random value and put the same value in the Vercel environment variable `VAPI_TOOL_SECRET`. Without it the endpoint stays switched off (HTTP 503), so nobody can use it to spend your Gemini quota.

## 2. Create the assistant

Assistants → Create → Blank.

- First message: `Hi, I'm the PlacidWay assistant. Ask me about treatments and prices at ITC Immunity Therapy Center in Tijuana.`
- System prompt:

```
You are a voice assistant for PlacidWay, a medical tourism platform.
For ANY question about treatments, clinics, packages, prices, locations or what is included,
call the ask_placidway tool with the caller's question, rewritten so it stands alone.
Never answer those from your own knowledge. Read the tool's answer naturally and keep it short.
If the tool says the information is not on the pages, say so and offer to have PlacidWay send a free quote.
Do not give medical advice; suggest speaking with their doctor.
You cannot book appointments; offer the free quote instead.
```

- Tools: add `ask_placidway`.
- Keep the default voice and transcriber. Publish.

## 3. Connect it to the website

Copy the **Public key** (Vapi → Organization → API keys, the public one, not the private one) and the assistant ID. Add them as Vercel **environment variables**:

- `VAPI_PUBLIC_KEY`
- `VAPI_ASSISTANT_ID`

Redeploy (`vercel deploy --prod`). A **🎙 Voice** button appears in the header. Voice transcripts show up in the chat.

Vapi gives new accounts free trial credit; check your balance in the dashboard before the review, and leave the voice button out (remove the variables) if credit runs low, so reviewers never hit a broken button.
