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
import time
import hashlib
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn

# Try to import LLM provider; fallback to mock if not available
try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

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

    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        self.model = model
        self.client = None
        if HAS_ANTHROPIC:
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if api_key:
                self.client = Anthropic(api_key=api_key)

    def compose(
        self,
        category: Dict[str, Any],
        merchant: Dict[str, Any],
        trigger: Dict[str, Any],
        customer: Optional[Dict[str, Any]] = None,
    ) -> ComposedMessage:
        """Compose a message using LLM or fallback heuristic."""

        prompt = self._build_prompt(category, merchant, trigger, customer)

        if self.client:
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1000,
                    temperature=0,  # Deterministic
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text.strip()
                return self._parse_response(text)
            except Exception as e:
                print(f"LLM error: {e}")
                return self._fallback_compose(category, merchant, trigger, customer)
        else:
            return self._fallback_compose(category, merchant, trigger, customer)

    def _build_prompt(
        self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]
    ) -> str:
        """Build the LLM prompt from 4 contexts."""
        prompt = f"""Compose a WhatsApp message from Vera to a merchant (or the merchant's customer).

CATEGORY: {category.get('slug', 'unknown')}
Category Voice: {category.get('voice', {}).get('tone', 'professional')}
Category Offers: {json.dumps([o.get('title') for o in category.get('offer_catalog', [])[:3]])}
Category Digest (this week's research): {json.dumps([d.get('title') for d in category.get('digest', [])[:2]])}

MERCHANT:
- Name: {merchant.get('identity', {}).get('name', 'unknown')}
- Owner: {merchant.get('identity', {}).get('owner_first_name', 'N/A')}
- Locality: {merchant.get('identity', {}).get('locality', 'unknown')}
- Languages: {merchant.get('identity', {}).get('languages', ['en'])}
- Performance (30d): {merchant.get('performance', {}).get('views', '?')} views, {merchant.get('performance', {}).get('calls', '?')} calls, CTR {merchant.get('performance', {}).get('ctr', '?')}
- Active Offers: {json.dumps([o.get('title') for o in merchant.get('offers', []) if o.get('status') == 'active'])}
- Customer Aggregate: {json.dumps(merchant.get('customer_aggregate', {}))}
- Signals: {merchant.get('signals', [])}
- Recent Conversation: {json.dumps(merchant.get('conversation_history', [])[-1:] if merchant.get('conversation_history') else [])}

TRIGGER:
- Kind: {trigger.get('kind', 'unknown')}
- Scope: {trigger.get('scope', 'merchant')}
- Urgency: {trigger.get('urgency', 2)}
- Payload: {json.dumps(trigger.get('payload', {}))}

CUSTOMER (if applicable):
{json.dumps(customer) if customer else 'None (merchant-facing only)'}

Your message should:
1. Use the merchant's owner first name if available
2. Reference specific numbers/dates from the context
3. Connect to the trigger clearly
4. Include one strong compulsion lever
5. Have a single binary or open-ended CTA
6. Match the category voice and vocabulary

RESPOND WITH ONLY THE JSON OBJECT (no markdown, no extra text)."""

        return prompt

    def _parse_response(self, text: str) -> ComposedMessage:
        """Parse LLM JSON response."""
        try:
            # Try to extract JSON from the response
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                data = json.loads(text)

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

    def _fallback_compose(
        self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]
    ) -> ComposedMessage:
        """Fallback heuristic-based composition when LLM unavailable."""
        merchant_name = merchant.get("identity", {}).get("name", "There")
        owner_first = merchant.get("identity", {}).get("owner_first_name")
        trigger_kind = trigger.get("kind", "")
        scope = trigger.get("scope", "merchant")

        # Build a reasonable fallback message
        if scope == "customer":
            body = f"Hi {customer.get('identity', {}).get('name', 'there')}, {owner_first or 'We'} here."
        else:
            body = f"Hi {owner_first or 'there'}!"

        body += f"\n\n{trigger_kind} update: something relevant to your business."

        return ComposedMessage(
            body=body,
            cta="open_ended",
            send_as="merchant_on_behalf" if scope == "customer" else "vera",
            suppression_key=f"{trigger_kind}:{merchant.get('merchant_id', 'unknown')}",
            rationale=f"Fallback message for {trigger_kind} trigger",
            template_name="vera_fallback_v1",
            template_params=[],
        )


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title="Vera Bot", version="1.0.0")

# In-memory stores
contexts: Dict[tuple, Dict[str, Any]] = {}  # (scope, context_id) -> {version, payload}
conversations: Dict[str, List[Dict]] = {}  # conversation_id -> turns
suppressed: set = set()  # suppression_keys already sent

# Metadata
START_TIME = time.time()

composer = VeraComposer()


# ============================================================================
# ENDPOINTS
# ============================================================================


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
        "model": "claude-3-5-sonnet-20241022",
        "approach": "Single LLM-powered composer with context dispatch by trigger.kind + fallback heuristics",
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

    # Detect auto-reply (same message verbatim 3+ times)
    conversation = conversations.get(body.conversation_id, [])
    recent_msgs = [
        turn["body"]
        for turn in conversation[-3:]
        if turn.get("from") in ["merchant", "customer"]
    ]

    auto_reply_detected = (
        len(recent_msgs) >= 2 and all(m == body.message for m in recent_msgs)
    )

    if auto_reply_detected:
        return {
            "action": "wait",
            "wait_seconds": 14400,
            "rationale": "Detected WhatsApp auto-reply pattern (same text 3x). Backing off 4 hours.",
        }

    # Check for intent signals
    intent_positive = any(
        word in body.message.lower()
        for word in ["yes", "ok", "sure", "let's do it", "go ahead", "confirmed"]
    )
    intent_negative = any(
        word in body.message.lower()
        for word in ["no", "stop", "unsubscribe", "not interested", "remove"]
    )

    if intent_negative:
        return {
            "action": "end",
            "rationale": "Merchant opted out. Closing conversation per preference.",
        }

    # For positive intent or continuation, compose follow-up
    if intent_positive:
        return {
            "action": "send",
            "body": "Great! I'm drafting the next steps for you now — should be ready in 2 minutes. I'll send it over shortly.",
            "cta": "none",
            "rationale": "Merchant committed. Advancing to execution.",
        }
    else:
        # Default: continue conversation
        return {
            "action": "send",
            "body": "Thanks for that. What else can I help you with?",
            "cta": "open_ended",
            "rationale": "Continuing conversation; seeking clarification or next action.",
        }


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
