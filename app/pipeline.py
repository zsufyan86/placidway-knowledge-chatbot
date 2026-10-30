"""The answering pipeline: retrieve -> (rewrite if needed) -> answer -> verify.

    question
      |  injection check (code)            -> polite refusal
      |  BM25 retrieval on question + previous user turn (handles follow-ups)
      |  if most query words are unknown to the pages (another language,
      |  vague wording): one LLM call rewrites it into an English query
      |  wrong-premise check (code): a price the user states that the pages
      |  don't give for that treatment is flagged to the model up front
      |  answer call: Gemini sees ONLY the retrieved chunks, returns JSON
      |  verify (code), against the CITED chunks only: citations exist, every
      |  price is on a cited page and sits next to the right treatment, no
      |  agreement with a wrong figure, no medical verdict, no leaks
      |  failed check -> one retry with the reason -> safe fallback
      |  booking / quote / medical / out-of-scope replies: fixed text from the app
      v
    answer + source links built by the server (the model never writes URLs)
"""

from __future__ import annotations

import logging
import re
import time

from . import config, guards, store
from .kb import KnowledgeBase
from .llm import LLMError, LLMUnavailable
from .prompts import (ANSWER_SCHEMA, ANSWER_SYSTEM, REWRITE_SCHEMA, REWRITE_SYSTEM,
                      answer_prompt, rewrite_prompt)
from .sources import ALLOWED_EXTRA_URLS, QUOTE_URL

log = logging.getLogger(__name__)

# Fixed replies, written by the app rather than the model, in the languages
# reviewers are most likely to try. Other languages get English fallbacks.
MESSAGES = {
    "en": {
        "disclaimer": "This assistant shares information from PlacidWay's pages and is not medical advice. "
                      "Please talk to your own doctor before deciding on any treatment.",
        "unverified": "I couldn't verify an answer to that from PlacidWay's pages, so I'd rather not guess. "
                      "You can request a free quote and the PlacidWay team will get back to you with exact details.",
        "medical": "I can't tell you whether a treatment is safe or right for your personal situation. "
                   "Please speak with your own doctor and the clinic's medical team. PlacidWay can put you in "
                   "touch with the clinic through a free quote request.",
        "unavailable": "I'm getting a lot of questions right now and can't answer reliably this minute. "
                       "Please try again shortly, or request a free quote and the PlacidWay team will reply directly.",
        "injection": "I can only help with questions about the treatments, clinic and prices on PlacidWay's pages. "
                     "What would you like to know about them?",
        "out_of_scope": "Sorry, I can only answer questions about the treatments, clinic and prices on PlacidWay's "
                        "pages, so I can't help with that one. Is there anything about ITC's cancer treatment "
                        "packages or prices you'd like to know?",
        "cannot_do": "I can't book appointments or check availability for specific dates. Please request a free "
                     "quote or contact PlacidWay, and the team will help you arrange it with the clinic.",
        "quote_request": "Happy to help. Leave your name and email in the form below and the PlacidWay team will "
                         "contact you with a free, personalised quote.",
        "see_doctor": "Please speak with your own doctor and the clinic's medical team about your situation.",
    },
    "es": {
        "disclaimer": "Este asistente comparte información de las páginas de PlacidWay y no es consejo médico. "
                      "Consulte a su propio médico antes de decidir cualquier tratamiento.",
        "unverified": "No pude verificar una respuesta en las páginas de PlacidWay, así que prefiero no adivinar. "
                      "Puede solicitar una cotización gratuita y el equipo de PlacidWay le dará los detalles exactos.",
        "medical": "No puedo decirle si un tratamiento es seguro o adecuado para su situación personal. "
                   "Hable con su propio médico y con el equipo médico de la clínica. PlacidWay puede ponerle en "
                   "contacto con la clínica mediante una solicitud de cotización gratuita.",
        "unavailable": "Estoy recibiendo muchas preguntas ahora mismo y no puedo responder con fiabilidad. "
                       "Inténtelo de nuevo en breve o solicite una cotización gratuita y el equipo de PlacidWay le responderá.",
        "injection": "Solo puedo ayudar con preguntas sobre los tratamientos, la clínica y los precios de las páginas "
                     "de PlacidWay. ¿Qué le gustaría saber?",
        "out_of_scope": "Lo siento, solo puedo responder preguntas sobre los tratamientos, la clínica y los precios de "
                        "las páginas de PlacidWay. ¿Quiere saber algo sobre los paquetes o precios de ITC?",
        "cannot_do": "No puedo reservar citas ni consultar disponibilidad en fechas concretas. Solicite una cotización "
                     "gratuita o contacte a PlacidWay y el equipo le ayudará a organizarlo con la clínica.",
        "quote_request": "Con gusto. Deje su nombre y correo en el formulario de abajo y el equipo de PlacidWay le "
                         "contactará con una cotización gratuita y personalizada.",
        "see_doctor": "Hable con su propio médico y con el equipo médico de la clínica sobre su situación.",
    },
    "ar": {
        "disclaimer": "يشارك هذا المساعد معلومات من صفحات PlacidWay وليس نصيحة طبية. "
                      "يرجى استشارة طبيبك قبل اتخاذ أي قرار بشأن العلاج.",
        "unverified": "لم أتمكن من التحقق من إجابة لهذا السؤال من صفحات PlacidWay، لذا أفضل عدم التخمين. "
                      "يمكنك طلب عرض سعر مجاني وسيتواصل معك فريق PlacidWay بالتفاصيل الدقيقة.",
        "medical": "لا أستطيع أن أخبرك إن كان العلاج آمنًا أو مناسبًا لحالتك الشخصية. "
                   "يرجى التحدث مع طبيبك ومع الفريق الطبي في العيادة. يمكن لـ PlacidWay أن يصلك بالعيادة عبر طلب عرض سعر مجاني.",
        "unavailable": "أتلقى الكثير من الأسئلة الآن ولا أستطيع الإجابة بدقة في هذه اللحظة. "
                       "يرجى المحاولة بعد قليل أو طلب عرض سعر مجاني وسيرد عليك فريق PlacidWay مباشرة.",
        "injection": "يمكنني المساعدة فقط في الأسئلة المتعلقة بالعلاجات والعيادة والأسعار في صفحات PlacidWay. ماذا تود أن تعرف؟",
        "out_of_scope": "عذرًا، يمكنني الإجابة فقط عن الأسئلة المتعلقة بالعلاجات والعيادة والأسعار في صفحات PlacidWay. "
                        "هل تود معرفة شيء عن باقات أو أسعار علاج السرطان في ITC؟",
        "cannot_do": "لا أستطيع حجز المواعيد أو التحقق من التوفر في تواريخ محددة. يرجى طلب عرض سعر مجاني أو التواصل "
                     "مع PlacidWay وسيساعدك الفريق في الترتيب مع العيادة.",
        "quote_request": "بكل سرور. اترك اسمك وبريدك الإلكتروني في النموذج أدناه وسيتواصل معك فريق PlacidWay "
                         "بعرض سعر مجاني ومخصص لك.",
        "see_doctor": "يرجى التحدث مع طبيبك ومع الفريق الطبي في العيادة بشأن حالتك.",
    },
}

VALID_STATUS = {"answered", "not_in_content", "medical_advice", "cannot_do", "quote_request", "out_of_scope"}
# Replies the app writes itself instead of using the model's words.
# Health questions are among them: the brief wants "no diagnosis, see a doctor",
# and this keeps the model from adding loose claims about someone's condition.
FIXED_REPLIES = {"out_of_scope", "cannot_do", "quote_request", "medical_advice"}
# Replies without any cited source must stay short: they should say "not on
# these pages" or decline, not explain things from the model's own knowledge.
MAX_UNCITED_CHARS = 600

# The visitor themself asks for a quote or to be contacted. The model's
# "quote_request" status is only trusted when this matches, so a real answer
# is never swapped for the quote form text.
_QUOTE_INTENT = re.compile(
    r"\b(quote|quotation|estimate for me|contact me|call me|reach me|email me|get in touch|someone contact|"
    r"cotizaci\w*|presupuesto|cont[aá]cte\w*|ll[aá]me\w*)\b|عرض سعر|تواصل|اتصل",
    re.I,
)
_FOLLOW_UP = re.compile(r"^\W*(and|also|then|what about|how about|same)\b", re.I)
_COMPARE = re.compile(
    r"\b(cheaper|more expensive|less expensive|compare\w*|comparison|versus|vs\.?|difference in price|"
    r"m[aá]s barato|m[aá]s caro|comparar)\b|أرخص|مقارنة",
    re.I,
)
_DOCTOR = re.compile(r"\b(doctor|physician|médico|medico)|طبيب", re.I)
_NON_LATIN = re.compile(r"[Ѐ-ӿ֐-ۿݐ-ݿऀ-ॿ぀-ヿ一-鿿가-힯]")
_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")
_SPANISH = re.compile(
    r"[¿¡ñ]|\b(cuánto|cuanto|cuesta|cuestan|precio|precios|tratamiento|qué|puedo|puede|cita|mañana|"
    r"clima|hola|gracias|incluye|incluido|paquete|es seguro|para mi|reservar)\b",
    re.I,
)
_OTHER_LATIN = re.compile(
    r"[¿¡ñçãõœß]|\b(el|la|los|las|del|que|qué|cuánto|cuanto|cuesta|precio|para|tratamiento|"
    r"combien|est|les|des|pour|quanto|custa|preço|und|der|die|das|wie|viel|kosten|ile|kaç)\b",
    re.I,
)


def detect_language(question: str, hint: str = "") -> str | None:
    """'en', 'es' or 'ar' for the fixed replies; None for other languages."""
    hint = hint.lower()
    if hint.startswith(("arab", "ar")) or _ARABIC.search(question):
        return "ar"
    if hint.startswith(("span", "es")) or _SPANISH.search(question):
        return "es"
    if (hint and not hint.startswith(("engl", "en"))) or _NON_LATIN.search(question):
        return None
    return "en"


def msg(key: str, lang: str | None) -> str:
    return MESSAGES.get(lang or "en", MESSAGES["en"])[key]


def clean_history(history: list | None) -> list[dict]:
    """Keep the last few turns. The browser sends the history, so drop any turn
    that tries to smuggle instructions in (a faked 'assistant' turn included)."""
    out = []
    for turn in (history or [])[-config.MAX_HISTORY_TURNS * 2 :]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = str(turn.get("content", ""))[: config.MAX_QUESTION_CHARS * 2]
        if role in ("user", "assistant") and content.strip() and not guards.looks_like_injection(content):
            out.append({"role": role, "content": content})
    return out


def _needs_rewrite(question: str, kb: KnowledgeBase, top_score: float) -> bool:
    """Spend the extra LLM call only when keyword search is likely to fail:
    another language, or nothing on the pages matched at all."""
    if _NON_LATIN.search(question):
        return True
    if _OTHER_LATIN.search(question) and kb.retriever.coverage(question) < 0.8:
        return True
    return top_score <= 0


def _evidence(chunks: list[dict], cites: list[int], kb: KnowledgeBase) -> list[dict]:
    """The chunks the answer may rely on: the cited ones plus the overview
    (headline price, location) of each cited page."""
    cited = [chunks[i - 1] for i in cites]
    ids = {c["id"] for c in cited}
    for c in list(cited):
        ov = kb.retriever.overview_by_url.get(c["url"])
        if ov and ov["id"] not in ids:
            cited.append(ov)
            ids.add(ov["id"])
    return cited


def _verify(data: dict, chunks: list[dict], question: str, history: list[dict],
            kb: KnowledgeBase, premise_bad: list[str]) -> tuple[bool, str, dict]:
    status = data.get("status") if data.get("status") in VALID_STATUS else "answered"
    answer = str(data.get("answer", "")).strip()
    cites = []
    for c in data.get("citations") or []:
        try:
            i = int(c)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(chunks) and i not in cites:
            cites.append(i)
    parsed = {"status": status, "answer": answer, "citations": cites,
              "wants_quote": bool(data.get("wants_quote")), "medical_topic": bool(data.get("medical_topic"))}

    if not answer:
        return False, "the answer was empty", parsed
    if status == "answered" and not cites:
        return False, "status is answered but no sources were cited", parsed
    if not cites and len(answer) > MAX_UNCITED_CHARS:
        return False, "an answer without sources must be short and only say what is not on the pages", parsed

    # Numbers are checked against what the answer CITES, not everything that
    # was retrieved, so a figure from an unrelated chunk can't slip through.
    evidence = _evidence(chunks, cites, kb)
    context = "\n".join(c["text"] + " " + c["heading"] for c in evidence)
    prev_user = [t["content"] for t in history if t["role"] == "user"][-1:]
    user_text = " ".join([question] + prev_user)
    bad = guards.unverified_numbers(answer, context, user_text)
    if bad:
        return False, f"these figures are not in the sources you cited: {', '.join(bad)}", parsed
    wrong_pair = guards.misattributed_figures(answer, evidence, kb.retriever.specific_terms,
                                              kb.retriever.page_terms)
    if wrong_pair:
        return False, (f"these prices belong to a different treatment, clinic or country in the sources: "
                       f"{', '.join('$' + f for f in wrong_pair)}"), parsed
    if guards.agrees_with_unsupported_figure(answer, premise_bad):
        return False, "the answer agrees with a figure the sources do not support", parsed
    if guards.gives_medical_verdict(answer):
        return False, "the answer gives a personal medical verdict", parsed
    if guards.leaks_instructions(answer, [config.GEMINI_API_KEY, config.ADMIN_TOKEN]):
        return False, "leak", parsed
    return True, "", parsed


def _sources(chunks: list[dict], cites: list[int]) -> list[dict]:
    seen, out = set(), []
    for i in cites:
        c = chunks[i - 1]
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        out.append({"url": c["url"], "title": c["page_title"], "type": c["page_type"]})
    return out


def respond(question: str, history: list | None, kb: KnowledgeBase, llm,
            disclaimer_shown: bool = False, channel: str = "web") -> dict:
    started = time.monotonic()
    question = (question or "").strip()[: config.MAX_QUESTION_CHARS]
    history = clean_history(history)
    lang = detect_language(question)
    reply = {"answer": "", "sources": [], "status": "answered", "wants_quote": False,
             "quote_url": None, "disclaimer": None, "model": getattr(llm, "active_model", None)}

    def finish(note: str = "") -> dict:
        if reply["status"] in ("not_in_content", "cannot_do", "quote_request", "unavailable") or reply["wants_quote"]:
            reply["quote_url"] = QUOTE_URL
        reply["model"] = getattr(llm, "active_model", None)
        store.record("chats", {
            "channel": channel, "question": question, "status": reply["status"],
            "sources": [s["url"] for s in reply["sources"]], "model": reply["model"],
            "ms": int((time.monotonic() - started) * 1000), "note": note,
        })
        if reply["status"] in ("not_in_content", "unavailable"):
            store.record("gaps", {"question": question, "status": reply["status"], "note": note})
        return reply

    if not question:
        reply.update(answer="Please type a question.", status="out_of_scope")
        return reply
    if not kb.ready:
        reply.update(answer="I'm still loading PlacidWay's pages. Please try again in a minute.",
                     status="unavailable")
        return finish("knowledge base not ready")
    if guards.looks_like_injection(question):
        reply.update(answer=msg("injection", lang), status="out_of_scope")
        return finish("injection pattern")

    prev_user = [t["content"] for t in history if t["role"] == "user"][-1:]
    # Search with the previous question only for real follow-ups. A question
    # that names its own treatment or place stands alone: mixing in an older
    # question ("cheaper in Turkey?") would pull the search toward its topic.
    follow_up = bool(_FOLLOW_UP.search(question)) or not guards.names_entity(question)
    search_query = " ".join([question] + (prev_user if follow_up else []))
    chunks, top = kb.retriever.context_for(search_query, config.TOP_K, config.MAX_PER_PAGE)

    try:
        if _needs_rewrite(question, kb, top):
            try:
                rw = llm.generate_json(REWRITE_SYSTEM, rewrite_prompt(question, history), REWRITE_SCHEMA)
                rewritten = str(rw.get("query", "")).strip()
                lang = detect_language(question, str(rw.get("language", "")))
                if rewritten:
                    chunks, top = kb.retriever.context_for(rewritten + " " + question,
                                                           config.TOP_K, config.MAX_PER_PAGE)
            except LLMError:
                pass  # keep the original retrieval

        # A price the user states that the pages don't give for that treatment
        # is flagged before the model answers, and checked again afterwards.
        premise_bad = guards.unsupported_premise_figures(
            question, chunks, kb.retriever.specific_terms, " ".join(prev_user))
        note = ""
        if premise_bad:
            figs = ", ".join("$" + f for f in premise_bad)
            note = (f"The figure(s) {figs} the user mentioned do not appear in the SOURCES for what they asked "
                    "about. Do not confirm them. Say the pages do not support that figure, then give the price the "
                    "SOURCES list for it, with citations.")
        if _COMPARE.search(question):
            note += (" This is a comparison question. If both sides have a price in the SOURCES, end with one "
                     "sentence saying which listed price is lower. Name the specific procedure the page lists for "
                     "each price (physiotherapy, ozone therapy ...), not just 'alternative medicine'. Call them "
                     "like-for-like only if the listed procedures are the same; otherwise say they are different "
                     "treatments. If one side is missing, say which one.")

        if history:
            # In a longer chat the model tends to carry on summarising instead
            # of admitting the follow-up isn't answered on the pages.
            note += (" Answer the LATEST USER MESSAGE itself, not the earlier questions. If the SOURCES answer it, "
                     "answer directly with status answered and do not say the pages lack it. Only if they truly "
                     "do not (for example, a program length does not answer a recovery time question), say so in "
                     "the first sentence with status not_in_content, then add closely related facts.")

        prompt = answer_prompt(question, history, chunks, note.strip())
        parsed, reason = None, ""
        for attempt in range(2):
            p = prompt if attempt == 0 else (
                prompt + f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED BY A CHECK: {reason}. "
                "Answer again using only figures and facts that appear in the SOURCES, keep each price with the "
                "treatment it belongs to, and cite the sources you use."
            )
            data = llm.generate_json(ANSWER_SYSTEM, p, ANSWER_SCHEMA)
            ok, reason, parsed = _verify(data, chunks, question, history, kb, premise_bad)
            if ok:
                break
            if reason == "leak":
                break
        else:
            ok = False
    except LLMUnavailable as exc:
        log.warning("LLM unavailable: %s", exc)
        reply.update(answer=msg("unavailable", lang), status="unavailable")
        return finish(str(exc)[:200])
    except LLMError as exc:
        reply.update(answer=msg("unverified", lang), status="not_in_content", wants_quote=True)
        return finish(f"llm error: {str(exc)[:150]}")

    medical = guards.is_personal_medical(question) or (parsed or {}).get("medical_topic")
    if not ok:
        if (parsed and parsed["status"] == "medical_advice") or guards.is_personal_medical(question) \
                or "medical verdict" in reason:
            reply.update(answer=msg("medical", lang), status="medical_advice", wants_quote=True)
        else:
            reply.update(answer=msg("unverified", lang), status="not_in_content", wants_quote=True)
        if medical and not disclaimer_shown:
            reply["disclaimer"] = msg("disclaimer", lang)
        return finish(f"check failed: {reason}")

    status = parsed["status"]
    if status == "quote_request" and not _QUOTE_INTENT.search(question):
        # The model reached for the quote form on its own: keep its checked
        # answer, just offer the quote button with it.
        status = "answered" if parsed["citations"] else "not_in_content"
        parsed["wants_quote"] = True
    allowed = {c["url"] for c in chunks} | ALLOWED_EXTRA_URLS
    reply.update(
        answer=guards.strip_source_markers(guards.scrub_links(parsed["answer"], allowed)),
        status=status,
        wants_quote=parsed["wants_quote"] or status in ("cannot_do", "quote_request"),
        sources=_sources(chunks, parsed["citations"]),
    )
    # Declining, "I can't book", "here's the quote form" and "ask your doctor"
    # are written by the app, so the model has no room to add facts there.
    if status in FIXED_REPLIES and lang:
        reply.update(answer=msg("medical" if status == "medical_advice" else status, lang), sources=[])
    # A personal medical question must always end with "see your doctor".
    if status == "medical_advice" and not _DOCTOR.search(reply["answer"]):
        reply["answer"] = reply["answer"].rstrip() + " " + msg("see_doctor", lang)
    if (medical or status == "medical_advice") and not disclaimer_shown:
        reply["disclaimer"] = msg("disclaimer", lang)
    return finish()
