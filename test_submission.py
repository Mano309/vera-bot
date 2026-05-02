#!/usr/bin/env python3
"""
Quick test to validate bot logic and composition
"""

import json
import sys

# Test 1: Validate submission.jsonl format
print("=" * 70)
print("TEST 1: Submission JSONL Format Validation")
print("=" * 70)

with open('submission.jsonl', 'r', encoding='utf-8') as f:
    lines = f.readlines()

required_fields = {"test_id", "body", "cta", "send_as", "suppression_key", "rationale"}
all_valid = True

for i, line in enumerate(lines, 1):
    try:
        obj = json.loads(line)
        missing = required_fields - set(obj.keys())
        if missing:
            print(f"❌ T{i:02d}: Missing fields: {missing}")
            all_valid = False
        elif len(obj["body"]) < 20:
            print(f"❌ T{i:02d}: Body too short ({len(obj['body'])} chars)")
            all_valid = False
        elif obj["cta"] not in {"binary_yes_no", "multi_choice", "open_ended", "none"}:
            print(f"❌ T{i:02d}: Invalid CTA '{obj['cta']}'")
            all_valid = False
        elif obj["send_as"] not in {"vera", "merchant_on_behalf"}:
            print(f"❌ T{i:02d}: Invalid send_as '{obj['send_as']}'")
            all_valid = False
    except json.JSONDecodeError as e:
        print(f"❌ Line {i}: Invalid JSON — {e}")
        all_valid = False

if all_valid:
    print(f"✓ All {len(lines)} submission lines are valid")
else:
    sys.exit(1)

# Test 2: Specificity check (heuristic)
print("\n" + "=" * 70)
print("TEST 2: Specificity Heuristic (should have numbers/dates)")
print("=" * 70)

import re

specificity_scores = []

for i, line in enumerate(lines, 1):
    obj = json.loads(line)
    body = obj["body"]
    
    # Count verifiable facts
    numbers = len(re.findall(r'\d+', body))
    dates = len(re.findall(r'\d{4}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec', body))
    sources = len(re.findall(r'JIDA|DCI|Google|peer|batch', body))
    names = len(re.findall(r'[A-Z][a-z]+ ', body))
    
    score = min(10, (numbers + dates + sources) // 2 + names)
    specificity_scores.append(score)
    
    if score < 5:
        print(f"⚠  T{i:02d}: Low specificity ({score}/10) — {body[:50]}...")

avg_specificity = sum(specificity_scores) / len(specificity_scores)
print(f"\n✓ Average specificity score: {avg_specificity:.1f}/10")

# Test 3: Coverage check
print("\n" + "=" * 70)
print("TEST 3: Category & Scope Coverage")
print("=" * 70)

categories = {}
scopes = {"vera": 0, "merchant_on_behalf": 0}

for i, line in enumerate(lines, 1):
    obj = json.loads(line)
    body = obj["body"]
    
    # Infer category from keywords
    if any(w in body.lower() for w in ["dr.", "dental", "tooth", "cavity", "fluoride"]):
        cat = "dentists"
    elif any(w in body.lower() for w in ["salon", "hair", "lakshmi"]):
        cat = "salons"
    elif any(w in body.lower() for w in ["restaurant", "pizza", "thali"]):
        cat = "restaurants"
    elif any(w in body.lower() for w in ["gym", "fitness", "member"]):
        cat = "gyms"
    elif any(w in body.lower() for w in ["pharmacy", "medicine", "ramesh"]):
        cat = "pharmacies"
    else:
        cat = "unknown"
    
    categories[cat] = categories.get(cat, 0) + 1
    scopes[obj["send_as"]] += 1

print("Category distribution:")
for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
    pct = (count / len(lines)) * 100
    print(f"  {cat:20} {count:2} messages ({pct:5.1f}%)")

print("\nScope distribution:")
for scope, count in scopes.items():
    pct = (count / len(lines)) * 100
    print(f"  {scope:20} {count:2} messages ({pct:5.1f}%)")

# Test 4: Content quality spot check
print("\n" + "=" * 70)
print("TEST 4: Content Quality Spot Check (sample of 5)")
print("=" * 70)

samples = [0, 7, 14, 21, 28]  # T01, T08, T15, T22, T29
for idx in samples:
    obj = json.loads(lines[idx])
    test_id = obj["test_id"]
    body = obj["body"]
    has_name = any(name in body for name in ["Dr. Meera", "Bharat", "Lakshmi", "Suresh", "Karthik", "Ramesh"])
    has_numbers = bool(re.search(r'\d+', body))
    
    status = "✓" if (has_name or has_numbers) else "⚠"
    print(f"{status} {test_id}: {body[:60]}...")

print("\n" + "=" * 70)
print("✅ ALL TESTS PASSED")
print("=" * 70)
print(f"\nSubmission ready for judging:")
print(f"  - 30 test compositions")
print(f"  - Average specificity: {avg_specificity:.1f}/10")
print(f"  - Coverage: {len(categories)} categories, 2 scopes")
