#!/usr/bin/env python3
"""Test OpenAI API integration with bot."""

import requests
import json

print("=" * 60)
print("TESTING OPENAI API INTEGRATION")
print("=" * 60)

# Push a test category context
print("\n1️⃣  Pushing category context...")
cat_response = requests.post("http://localhost:8080/v1/context", json={
    "scope": "category",
    "context_id": "restaurants_test_v2",
    "version": 2,
    "payload": {
        "slug": "restaurants",
        "name": "Restaurants",
        "voice": {"tone": "casual_friendly"},
        "offer_catalog": [{"title": "Dine-in discount"}, {"title": "Delivery waiver"}],
        "digest": [{"title": "Weekend trends"}]
    },
    "delivered_at": "2026-05-02T12:00:00Z"
})
print(f"✓ Category accepted: {cat_response.json()['accepted']}")

# Push a merchant context
print("\n2️⃣  Pushing merchant context...")
merc_response = requests.post("http://localhost:8080/v1/context", json={
    "scope": "merchant",
    "context_id": "merchant_test_v2",
    "version": 2,
    "payload": {
        "merchant_id": "merchant_123",
        "category_slug": "restaurants_test_v2",
        "identity": {
            "name": "Spice House",
            "owner_first_name": "Priya",
            "locality": "Bangalore",
            "languages": ["en", "hi"]
        },
        "performance": {"views": 1250, "calls": 45, "ctr": 0.036},
        "offers": [{"title": "50% discount", "status": "active"}],
        "customer_aggregate": {"total_customers": 342, "repeat_rate": 0.28}
    },
    "delivered_at": "2026-05-02T12:00:00Z"
})
print(f"✓ Merchant accepted: {merc_response.json()['accepted']}")

# Push a trigger context
print("\n3️⃣  Pushing trigger context...")
trig_response = requests.post("http://localhost:8080/v1/context", json={
    "scope": "trigger",
    "context_id": "trigger_test_v2",
    "version": 2,
    "payload": {
        "trigger_id": "trigger_xyz",
        "kind": "perf_dip",
        "scope": "merchant",
        "urgency": 8,
        "merchant_id": "merchant_123",
        "payload": {"views_dip": 35, "week": "Apr 25-May 1"}
    },
    "delivered_at": "2026-05-02T12:00:00Z"
})
print(f"✓ Trigger accepted: {trig_response.json()['accepted']}")

# Now tick to generate a message
print("\n4️⃣  Calling /v1/tick to trigger LLM composition...")
tick_response = requests.post("http://localhost:8080/v1/tick", json={
    "now": "2026-05-02T12:25:00Z",
    "available_triggers": ["trigger_test_v2"]
})

print("\n" + "=" * 60)
print("✅ TICK RESPONSE (LLM-composed with OpenAI)")
print("=" * 60)

actions = tick_response.json()['actions']
if actions:
    print(json.dumps(actions[0], indent=2))
    print("\n" + "=" * 60)
    print("📱 MESSAGE BODY")
    print("=" * 60)
    print(actions[0]['body'])
    print("\n✅ OpenAI API is working! Message composed successfully.")
else:
    print("(No actions generated - check logs)")
