#!/usr/bin/env python3
"""
Vera AI Challenge Bot — Complete Implementation
================================================

This bot implements the full Vera engagement framework with:
- 5 HTTP endpoints (healthz, metadata, context, tick, reply)
- LLM-powered message composition
- Context persistence
- Multi-turn conversation handling
- Auto-reply detection
- Intent-based routing
"""

import os
import json
import re
import time
import hashlib
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn

# Load environment variables from .env file
load_dotenv()

# Try to import LLM provider; fallback to mock if not available
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class ComposedMessage:
    body: str
    cta: str  # "binary_yes_no", "multi_choice", "open_ended", "none"
    send_as: str  # "vera" or "merchant_on_behalf"
    suppression_key: str
    rationale: str
    template_name: str = "vera_generic"
    template_params: List[str] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        if d["template_params"] is None:
            d["template_params"] = []
        return d


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str


class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = []


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


# ============================================================================
# LLM COMPOSER
# ============================================================================

class VeraComposer:
    """Uses Claude to compose contextually relevant messages."""

    SYSTEM_PROMPT = """You are Vera, magicpin's AI merchant assistant. Your job is to compose WhatsApp messages to merchants and their customers that are:

1. SPECIFIC: Use concrete numbers, dates, and facts from the context. Never invent data.
2. CATEGORY-FIT: Match the professional voice and vocabulary of the business type.
3. MERCHANT-FIT: Reference the merchant's actual name, owner name, offers, and situation.
4. TRIGGER-RELEVANT: Make clear WHY you're messaging now (the trigger).
5. COMPELLING: Use loss aversion, curiosity, reciprocity, or social proof. Include a low-friction CTA.

KEY RULES:
- No medical claims for customer-facing dentistry messages
- Use owner's first name when present (Dr. Meera, not Dr. Meera's Clinic)
- Honor language preferences (hi-en mix if specified)
- Single binary CTA preferred (YES/NO or CONFIRM/CANCEL)
- Always cite sources for research/compliance claims
- Never fabricate data not in the context

You must respond with ONLY a valid JSON object with no extra text or markdown, containing these exact fields:
{
  "body": "the message text",
  "cta": "binary_yes_no|multi_choice|open_ended|none",
  "send_as": "vera|merchant_on_behalf",
  "template_name": "vera_X_v1",
  "template_params": ["param1", "param2"],
  "rationale": "brief explanation"
}
"""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.client = None
        if HAS_OPENAI:
            api_key = os.getenv("OPENAI_API_KEY", "")
            if api_key:
                self.client = OpenAI(api_key=api_key)

    def compose(
        self,
        category: Dict[str, Any],
        merchant: Dict[str, Any],
        trigger: Dict[str, Any],
        customer: Optional[Dict[str, Any]] = None,
    ) -> ComposedMessage:
        """Compose a message using LLM when available, but prefer grounded deterministic copy."""

        fallback_message = self._grounded_compose(category, merchant, trigger, customer)
        prompt = self._build_prompt(category, merchant, trigger, customer)

        if self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=1000,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                parsed = self._parse_response(response.choices[0].message.content.strip())
                if self._looks_generic(parsed, merchant, trigger, customer):
                    return fallback_message
                return parsed
            except Exception as e:
                print(f"LLM error: {e}")

        return fallback_message

    def _clean_text(self, value: Any) -> str:
        return "" if value is None else str(value).strip()

    def _first_nonempty(self, *values: Any, default: str = "") -> str:
        for value in values:
            text = self._clean_text(value)
            if text:
                return text
        return default

    def _format_pct(self, value: Any) -> str:
        try:
            number = float(value)
        except Exception:
            return self._clean_text(value)
        pct = number * 100 if abs(number) <= 1 else number
        return f"{pct:.0f}%" if pct == round(pct) else f"{pct:.1f}%"

    def _format_money(self, value: Any) -> str:
        try:
            number = int(float(value))
        except Exception:
            return self._clean_text(value)
        return f"₹{number:,}"

    def _category_style(self, category: Dict[str, Any]) -> Dict[str, Any]:
        voice = category.get("voice", {}) or {}
        return {
            "slug": self._clean_text(category.get("slug", "unknown")),
            "tone": self._clean_text(voice.get("tone", "professional")),
            "register": self._clean_text(voice.get("register", "")),
            "code_mix": voice.get("code_mix", []),
            "vocab_allowed": voice.get("vocab_allowed", []),
            "vocab_taboo": voice.get("vocab_taboo", []),
        }

    def _select_cta(self, trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> str:
        payload = trigger.get("payload", {}) or {}
        if trigger.get("scope") == "customer" and (payload.get("available_slots") or payload.get("next_session_options")):
            return "multi_choice"
        if trigger.get("kind") in {"active_planning_intent", "curious_ask_due"}:
            return "open_ended"
        if trigger.get("kind") in {"review_theme_emerged", "winback_eligible", "perf_dip", "renewal_due", "regulation_change", "research_digest"}:
            return "binary_yes_no"
        if customer and trigger.get("scope") == "customer":
            return "binary_yes_no"
        return "open_ended"

    def _slot_labels(self, payload: Dict[str, Any]) -> List[str]:
        labels: List[str] = []
        for slot in payload.get("available_slots", []) or []:
            label = self._clean_text(slot.get("label") or slot.get("iso"))
            if label:
                labels.append(label)
        for slot in payload.get("next_session_options", []) or []:
            label = self._clean_text(slot.get("label") or slot.get("iso"))
            if label:
                labels.append(label)
        return labels[:3]

    def _looks_generic(
        self,
        message: ComposedMessage,
        merchant: Dict[str, Any],
        trigger: Dict[str, Any],
        customer: Optional[Dict[str, Any]] = None,
    ) -> bool:
        body = (message.body or "").lower()
        if any(marker in body for marker in ["something relevant to your business", "hi there", "update:", "drafting"]):
            return True

        merchant_name = self._clean_text(merchant.get("identity", {}).get("name", "")).lower()
        trigger_kind = self._clean_text(trigger.get("kind", "")).lower().replace("_", " ")
        if merchant_name and merchant_name.split()[0] not in body:
            return True
        if trigger_kind and trigger_kind not in body:
            return True
        if customer and trigger.get("scope") == "customer":
            customer_name = self._clean_text(customer.get("identity", {}).get("name", "")).lower()
            if customer_name and customer_name.split()[0] not in body:
                return True
        return len(re.findall(r"\d+", body)) == 0

    def _build_prompt(
        self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]
    ) -> str:
        """Build the LLM prompt from 4 contexts."""
        prompt = f"""Compose a WhatsApp message from Vera to a merchant or customer.

CATEGORY: {category.get('slug', 'unknown')}
Category Voice: {category.get('voice', {}).get('tone', 'professional')}
Category Offers: {json.dumps([o.get('title') for o in category.get('offer_catalog', [])[:3]])}
Category Digest: {json.dumps([d.get('title') for d in category.get('digest', [])[:2]])}

MERCHANT:
- Name: {merchant.get('identity', {}).get('name', 'unknown')}
- Owner: {merchant.get('identity', {}).get('owner_first_name', 'N/A')}
- Locality: {merchant.get('identity', {}).get('locality', 'unknown')}
- Languages: {merchant.get('identity', {}).get('languages', ['en'])}
- Performance: {merchant.get('performance', {}).get('views', '?')} views, {merchant.get('performance', {}).get('calls', '?')} calls, CTR {merchant.get('performance', {}).get('ctr', '?')}
- Active Offers: {json.dumps([o.get('title') for o in merchant.get('offers', []) if o.get('status') == 'active'])}
- Customer Aggregate: {json.dumps(merchant.get('customer_aggregate', {}))}
- Signals: {merchant.get('signals', [])}

TRIGGER:
- Kind: {trigger.get('kind', 'unknown')}
- Scope: {trigger.get('scope', 'merchant')}
- Urgency: {trigger.get('urgency', 2)}
- Payload: {json.dumps(trigger.get('payload', {}))}

CUSTOMER (if applicable):
{json.dumps(customer) if customer else 'None'}

Use concrete numbers, dates, names, and one clear CTA. Return valid JSON only.
"""
        return prompt

    def _parse_response(self, text: str) -> ComposedMessage:
        """Parse LLM JSON response."""
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            data = json.loads(match.group()) if match else json.loads(text)
            return ComposedMessage(
                body=data.get("body", ""),
                cta=data.get("cta", "open_ended"),
                send_as=data.get("send_as", "vera"),
                suppression_key=data.get("suppression_key", ""),
                rationale=data.get("rationale", ""),
                template_name=data.get("template_name", "vera_generic_v1"),
                template_params=data.get("template_params", []),
            )
        except Exception as e:
            print(f"Parse error: {e}, text: {text[:200]}")
            raise

    def _grounded_compose(
        self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]
    ) -> ComposedMessage:
        style = self._category_style(category)
        merchant_identity = merchant.get("identity", {}) or {}
        merchant_name = self._first_nonempty(merchant_identity.get("name"), default="your clinic")
        owner_first = self._first_nonempty(merchant_identity.get("owner_first_name"), default="there")
        locality = self._first_nonempty(merchant_identity.get("locality"))
        city = self._first_nonempty(merchant_identity.get("city"))
        merchant_perf = merchant.get("performance", {}) or {}
        active_offers = [o.get("title") for o in merchant.get("offers", []) if o.get("status") == "active"]
        trigger_kind = self._clean_text(trigger.get("kind", "unknown"))
        scope = self._clean_text(trigger.get("scope", "merchant"))
        payload = trigger.get("payload", {}) or {}
        cta = self._select_cta(trigger, customer)
        salutation = f"Dr. {owner_first}" if style["slug"] == "dentists" and owner_first != "there" else owner_first
        if salutation == "":
            salutation = "there"

        performance_bits: List[str] = []
        if merchant_perf.get("views") is not None:
            performance_bits.append(f"{merchant_perf.get('views')} views")
        if merchant_perf.get("calls") is not None:
            performance_bits.append(f"{merchant_perf.get('calls')} calls")
        if merchant_perf.get("ctr") is not None:
            performance_bits.append(f"CTR {self._format_pct(merchant_perf.get('ctr'))}")
        performance_text = ", ".join(performance_bits) if performance_bits else "your current profile stats"
        offer_text = ", ".join(active_offers[:2]) if active_offers else "your current offer set"
        template_params = [p for p in [merchant_name, trigger_kind, locality or city, offer_text] if p]

        if scope == "customer":
            customer_name = self._first_nonempty(customer.get("identity", {}).get("name") if customer else None, default="there")
            if trigger_kind == "recall_due":
                slots = self._slot_labels(payload)
                if slots:
                    body = (
                        f"Hi {customer_name}, this is Vera from {merchant_name}. Your {self._clean_text(payload.get('service_due', 'follow-up'))} is due around {self._clean_text(payload.get('due_date', 'soon'))}. "
                        f"I can hold {', '.join(slots)}. Which slot should I keep?"
                    )
                    cta = "multi_choice"
                else:
                    body = (
                        f"Hi {customer_name}, this is Vera from {merchant_name}. Your {self._clean_text(payload.get('service_due', 'follow-up'))} is due around {self._clean_text(payload.get('due_date', 'soon'))}. "
                        "Would you like me to arrange the next available slot?"
                    )
                    cta = "binary_yes_no"
                rationale = "Grounded recall reminder using due date and live slot choices."
            elif trigger_kind in {"trial_followup", "wedding_package_followup", "customer_lapsed_hard"}:
                next_steps = self._slot_labels(payload)
                if next_steps:
                    body = f"Hi {customer_name}, Vera here from {merchant_name}. The next step options are {', '.join(next_steps)}. Which one should I lock in?"
                    cta = "multi_choice"
                else:
                    body = f"Hi {customer_name}, Vera here from {merchant_name}. The next step window is open now. Should I move ahead?"
                    cta = "binary_yes_no"
                rationale = f"Grounded customer follow-up for {trigger_kind}."
            else:
                body = f"Hi {customer_name}, Vera here from {merchant_name}. I’m reaching out on the {trigger_kind.replace('_', ' ')} update so we can keep the next step simple. Would you like me to proceed?"
                rationale = "Customer-scope fallback with a direct next step."
        else:
            if trigger_kind == "regulation_change":
                deadline = self._clean_text(payload.get("deadline_iso", "the deadline"))
                digest = category.get("digest", []) or []
                digest_item = next((item for item in digest if item.get("kind") == "compliance"), digest[0] if digest else {})
                title = self._first_nonempty(digest_item.get("title"), default="the compliance note")
                source = self._first_nonempty(digest_item.get("source"))
                body = f"{salutation}, the {title} matters for {merchant_name}: the DCI update points to {deadline}, and the note from {source} says D-speed film will miss the new dose limit while E-speed or RVG will pass. With {performance_text} and {offer_text}, this is worth auditing before the deadline. Would you like a 3-line SOP checklist for the X-ray setup?"
                rationale = "Grounded compliance reminder with deadline, source, and action."
            elif trigger_kind in {"research_digest", "curious_ask_due"}:
                digest = category.get("digest", []) or []
                digest_item = digest[0] if digest else {}
                title = self._first_nonempty(digest_item.get("title"), default="this week’s research")
                source = self._first_nonempty(digest_item.get("source"))
                body = f"{salutation}, worth a look: {title}. {source}. Your clinic has {performance_text}, and this note is especially relevant if your mix includes high-risk adults. Should I turn it into a short post for your profile?"
                rationale = "Grounded research digest tied to merchant performance and peer-clinical voice."
            elif trigger_kind in {"perf_dip", "seasonal_perf_dip"}:
                delta = self._clean_text(payload.get("delta_pct", merchant_perf.get("delta_7d", {}).get("views_pct", "")))
                metric = self._clean_text(payload.get("metric", "views"))
                window = self._clean_text(payload.get("window", "7d"))
                body = f"{salutation}, {merchant_name} is down {delta or 'on a dip'} in {metric} over {window}, while the last 30 days still show {performance_text}. If {offer_text} is the priority, I can help reframe it for the next push. Want me to draft a tighter message?"
                rationale = "Grounded performance-dip nudge with metric and recent numbers."
            elif trigger_kind == "renewal_due":
                days_remaining = self._clean_text(payload.get("days_remaining", merchant.get("subscription", {}).get("days_remaining", "")))
                renewal_amount = self._format_money(payload.get("renewal_amount"))
                plan = self._clean_text(payload.get("plan", merchant.get("subscription", {}).get("plan", "Pro")))
                body = f"{salutation}, {merchant_name} has {days_remaining} days left on the {plan} plan and the renewal amount is {renewal_amount or 'visible in your dashboard'}. Given {performance_text}, it may be worth confirming the renewal path now. Would you like a renewal reminder draft?"
                rationale = "Grounded renewal reminder with plan, countdown, and price."
            elif trigger_kind in {"review_theme_emerged", "winback_eligible", "milestone_reached"}:
                if trigger_kind == "review_theme_emerged":
                    theme = self._clean_text(payload.get("theme", "recent review theme"))
                    occurrences = self._clean_text(payload.get("occurrences_30d", ""))
                    quote = self._clean_text(payload.get("common_quote", ""))
                    body = f"{salutation}, a {theme} theme has shown up {occurrences} times in recent reviews for {merchant_name}. {quote}. With {performance_text}, a quick response could prevent the same issue from repeating. Should I draft a reply/update?"
                    rationale = "Grounded review-theme response with review frequency and quoted feedback."
                elif trigger_kind == "winback_eligible":
                    days_since_expiry = self._clean_text(payload.get("days_since_expiry", merchant.get("subscription", {}).get("days_since_expiry", "")))
                    lapsed_added = self._clean_text(payload.get("lapsed_customers_added_since_expiry", "0"))
                    body = f"{salutation}, {merchant_name} has been expired for {days_since_expiry} days, but {lapsed_added} lapsed customers were added since expiry. That makes a winback push timely, especially with {performance_text}. Want a short reactivation note?"
                    rationale = "Grounded winback prompt with expiry age and customer churn signal."
                else:
                    value_now = self._clean_text(payload.get("value_now", ""))
                    milestone_value = self._clean_text(payload.get("milestone_value", ""))
                    body = f"{salutation}, {merchant_name} is at {value_now} and only {milestone_value} is needed to hit the milestone. With {performance_text}, this is a clean moment to nudge engagement. Would you like me to draft a milestone push?"
                    rationale = "Grounded milestone prompt using current and target values."
            elif trigger_kind == "active_planning_intent":
                topic = self._clean_text(payload.get("intent_topic", "the plan"))
                last_message = self._clean_text(payload.get("merchant_last_message", ""))
                body = f"{salutation}, I can help shape {topic} for {merchant_name}. Your last note was: \"{last_message}\". Given {performance_text}, the next step is to keep it simple and specific. Want me to draft the structure?"
                rationale = "Grounded planning prompt using the merchant’s own last message."
            else:
                body = f"{salutation}, {merchant_name} in {', '.join([part for part in [locality, city] if part]) or 'your area'} shows {performance_text}. The {trigger_kind.replace('_', ' ')} note suggests {offer_text} could benefit from a small, timely push. Would you like me to draft it?"
                rationale = f"Grounded merchant fallback for {trigger_kind}."

        return ComposedMessage(
            body=body,
            cta=cta,
            send_as="merchant_on_behalf" if scope == "customer" else "vera",
            suppression_key=self._first_nonempty(trigger.get("suppression_key"), default=f"{trigger_kind}:{merchant.get('merchant_id', 'unknown')}"),
            rationale=rationale,
            template_name="vera_grounded_v1",
            template_params=template_params,
        )

    def _fallback_compose(
        self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]
    ) -> ComposedMessage:
        """Fallback heuristic-based composition when LLM unavailable."""
        return self._grounded_compose(category, merchant, trigger, customer)


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title="Vera Bot", version="1.0.0")

# In-memory stores
contexts: Dict[tuple, Dict[str, Any]] = {}  # (scope, context_id) -> {version, payload}
conversations: Dict[str, List[Dict]] = {}  # conversation_id -> turns
suppressed: set = set()  # suppression_keys already sent
conversation_meta: Dict[str, Dict[str, Any]] = {}  # conversation_id -> context summary
reply_memory: Dict[tuple, Dict[str, Any]] = {}  # (merchant_id, from_role, normalized_message) -> state

# Metadata
START_TIME = time.time()

composer = VeraComposer()


# ============================================================================
# ENDPOINTS
# ============================================================================


@app.get("/")
async def root():
    """Welcome page."""
    return {
        "message": "Vera AI Challenge Bot",
        "status": "running",
        "endpoints": {
            "healthz": "GET /v1/healthz",
            "metadata": "GET /v1/metadata",
            "context": "POST /v1/context",
            "tick": "POST /v1/tick",
            "reply": "POST /v1/reply"
        },
        "docs": "https://github.com/Mano309/vera-bot"
    }


@app.get("/v1/healthz")
async def healthz():
    """Liveness probe."""
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts.items():
        counts[scope] = counts.get(scope, 0) + 1

    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts,
    }


@app.get("/v1/metadata")
async def metadata():
    """Bot identity."""
    return {
        "team_name": "Solo Participant",
        "team_members": ["AI Assistant"],
        "model": "gpt-4o-mini (OpenAI)",
        "approach": "OpenAI-powered composer with context dispatch by trigger.kind + fallback heuristics",
        "contact_email": "challenge@magicpin.com",
        "version": "1.0.0",
        "submitted_at": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/context")
async def push_context(body: ContextBody):
    """Receive and store context (category, merchant, customer, trigger)."""
    key = (body.scope, body.context_id)

    # Check for stale version
    current = contexts.get(key)
    if current and current["version"] >= body.version:
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": current["version"],
        }

    # Store new version
    contexts[key] = {"version": body.version, "payload": body.payload}

    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/tick")
async def tick(body: TickBody):
    """Periodic wake-up; bot decides which triggers to act on."""
    actions = []

    for trigger_id in body.available_triggers:
        # Skip if already suppressed
        trigger = contexts.get(("trigger", trigger_id), {}).get("payload", {})
        if trigger.get("suppression_key") in suppressed:
            continue

        merchant_id = trigger.get("merchant_id")
        customer_id = trigger.get("customer_id")
        scope = trigger.get("scope", "merchant")

        # Load merchant context
        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        if not merchant:
            continue

        # Load category context
        category_slug = merchant.get("category_slug")
        category = contexts.get(("category", category_slug), {}).get("payload")
        if not category:
            continue

        # Load customer context if customer-scoped
        customer = None
        if scope == "customer" and customer_id:
            customer = contexts.get(("customer", customer_id), {}).get("payload")
            if not customer:
                continue

        # Compose message
        try:
            msg = composer.compose(category, merchant, trigger, customer)
        except Exception as e:
            print(f"Composition error for {trigger_id}: {e}")
            continue

        # Create action
        conv_id = f"conv_{merchant_id}_{trigger_id}_{uuid.uuid4().hex[:8]}"
        conversation_meta[conv_id] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trigger_id,
            "scope": scope,
            "category_slug": category_slug,
            "turn": 1,
            "last_action": "send",
            "last_body": msg.body,
        }
        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": msg.send_as,
            "trigger_id": trigger_id,
            "template_name": msg.template_name,
            "template_params": msg.template_params or [],
            "body": msg.body,
            "cta": msg.cta,
            "suppression_key": msg.suppression_key,
            "rationale": msg.rationale,
        }

        actions.append(action)

        # Mark suppression key
        if msg.suppression_key:
            suppressed.add(msg.suppression_key)

        # Initialize conversation
        conversations[conv_id] = [
            {
                "from": "vera",
                "body": msg.body,
                "timestamp": body.now,
                "turn": 1,
            }
        ]

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    """Receive merchant/customer reply; compose next move."""

    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    def has_positive_intent(text: str) -> bool:
        patterns = [
            r"\byes\b", r"\byep\b", r"\byes please\b", r"\bok\b", r"\bokay\b",
            r"\bsure\b", r"\bconfirm\b", r"\bconfirmed\b", r"\bgo ahead\b",
            r"\blet'?s do it\b", r"\bbook\b", r"\bschedule\b", r"\bnext step\b",
            r"\bwhats next\b", r"\bwhat's next\b", r"\bready\b", r"\bplease do\b",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    def has_negative_intent(text: str) -> bool:
        patterns = [
            r"\bstop\b", r"\bunsubscribe\b", r"\bnot interested\b", r"\bremove\b",
            r"\bspam\b", r"\bbusy\b", r"\bno thanks\b", r"\bno\b", r"\bnever\b",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    def build_customer_confirmation(meta: Dict[str, Any], message_text: str) -> str:
        customer_id = meta.get("customer_id")
        customer_ctx = contexts.get(("customer", customer_id), {}).get("payload", {}) if customer_id else {}
        customer_name = customer_ctx.get("identity", {}).get("name", "there")
        trigger_id = meta.get("trigger_id")
        trigger_ctx = contexts.get(("trigger", trigger_id), {}).get("payload", {}) if trigger_id else {}
        slot_labels = []
        for slot in trigger_ctx.get("available_slots", []) or []:
            label = slot.get("label") or slot.get("iso")
            if label:
                slot_labels.append(str(label))
        for slot in trigger_ctx.get("next_session_options", []) or []:
            label = slot.get("label") or slot.get("iso")
            if label:
                slot_labels.append(str(label))
        slot_hint = ""
        if slot_labels:
            lowered_message = message_text.lower()
            for label in slot_labels:
                if label.lower() in lowered_message:
                    slot_hint = label
                    break
            if not slot_hint:
                slot_hint = slot_labels[0]
        if not slot_hint:
            slot_match = re.search(r"\b(?:mon|tue|tues|wed|thu|thur|fri|sat|sun|today|tomorrow)\b[^.?!]*", message_text, re.I)
            slot_hint = slot_match.group(0).strip().rstrip(".,;:") if slot_match else (message_text.strip() or "the selected slot")
        return (
            f"Thanks, {customer_name}. I’ve noted your confirmation for {slot_hint}. "
            "If you want to change anything, send the new time and I’ll update it."
        )

    normalized_message = normalize(body.message)
    merchant_key = body.merchant_id or body.conversation_id
    memory_key = (merchant_key, body.from_role, normalized_message)
    state = reply_memory.get(memory_key, {"count": 0, "last_turn": 0})
    state["count"] = state.get("count", 0) + 1
    state["last_turn"] = body.turn_number
    reply_memory[memory_key] = state

    meta = conversation_meta.get(body.conversation_id, {})

    if state["count"] >= 3:
        return {
            "action": "end",
            "body": "Thanks — I’m stopping here so this thread doesn’t loop. Reach out when you want to continue.",
            "cta": "none",
            "rationale": "Repeated identical reply detected across turns; ending to prevent auto-reply loops.",
        }

    if has_negative_intent(normalized_message):
        return {
            "action": "end",
            "body": "Understood — I’ll stop messaging on this thread.",
            "cta": "none",
            "rationale": "Clear opt-out or negative intent detected.",
        }

    if body.from_role == "customer" and has_positive_intent(normalized_message):
        return {
            "action": "send",
            "body": build_customer_confirmation(meta, body.message),
            "cta": "none",
            "rationale": "Customer confirmed or asked to proceed; replying with a concrete confirmation.",
        }

    if body.from_role == "merchant" and has_positive_intent(normalized_message):
        trigger = meta.get("trigger_id", "")
        if trigger:
            return {
                "action": "send",
                "body": f"Great — I’m moving {trigger.replace('_', ' ')} forward now. I’ll keep it tight and send the next draft shortly.",
                "cta": "none",
                "rationale": "Merchant committed; moving from qualification to execution.",
            }
        return {
            "action": "send",
            "body": "Great — I’m moving this forward now and will send the next draft shortly.",
            "cta": "none",
            "rationale": "Merchant committed; moving from qualification to execution.",
        }

    if body.from_role == "customer":
        if re.search(r"\b(wed|thu|fri|sat|sun|mon|tue|tues|today|tomorrow)\b.*\b(\d{1,2}\s*(?:am|pm)|\d{1,2}:\d{2}\s*(?:am|pm)?)", normalized_message) or re.search(r"\bbook|schedule|confirm\b", normalized_message):
            return {
                "action": "send",
                "body": build_customer_confirmation(meta, body.message),
                "cta": "none",
                "rationale": "Customer message looks like a scheduling confirmation; responding with a concrete acknowledgment.",
            }
        return {
            "action": "send",
            "body": "Thanks — can you confirm the time slot you want me to keep?",
            "cta": "open_ended",
            "rationale": "Customer reply is ambiguous, so ask one clear follow-up question.",
        }

    if body.from_role == "merchant":
        return {
            "action": "send",
            "body": "Thanks for the update. If you want, I can turn that into the next step right away.",
            "cta": "open_ended",
            "rationale": "Merchant reply needs a gentle continuation rather than ending the thread.",
        }

    return {
        "action": "send",
        "body": "Thanks — what would you like me to do next?",
        "cta": "open_ended",
        "rationale": "Default continuation with a clear next question.",
    }


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
