#!/usr/bin/env python3
import requests
import json

print("Testing basic endpoints...\n")

# Test health
r = requests.get("http://localhost:8080/v1/healthz")
print("✓ /v1/healthz:")
print(json.dumps(r.json(), indent=2))

# Test metadata
r = requests.get("http://localhost:8080/v1/metadata")
print("\n✓ /v1/metadata:")
print(json.dumps(r.json(), indent=2))
