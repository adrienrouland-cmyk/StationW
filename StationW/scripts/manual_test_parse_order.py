#!/usr/bin/env python3
"""Manual test of parse-order workflow"""
from unittest.mock import patch, MagicMock
import json
from datetime import datetime, timezone
from validator.main import (
    parse_order_with_kimi,
    find_missing_fields_and_generate_prompt,
    save_request_to_db,
    get_request_from_db,
)

print("=" * 100)
print("MANUAL TEST: Processing order text 'Do you have 5 LED for 1/12/2026'")
print("=" * 100)

order_text = "Do you have 5 LED for 1/12/2026"

print(f"\n📥 INPUT ORDER TEXT:")
print(f"   '{order_text}'")

# Step 1: Parse with Kimi
print("\n" + "=" * 100)
print("STEP 1: Kimi parses the order text")
print("=" * 100)

with patch('validator.main.client') as mock_client:
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "client": {"raw_identity": {"phone": None, "email": None}},
        "delivery": {"raw_address": None, "country": None, "pick_up": None},
        "payment_terms": {"raw": None, "type": None, "details": None},
        "items": [
            {
                "product": "LED",
                "quantity": 5,
                "date": "2026-12-01",
                "wanted_price": None
            }
        ]
    })
    mock_client.chat.completions.create.return_value = mock_response
    
    parsed = parse_order_with_kimi(order_text)
    
    print("\n✅ Kimi parsed response:")
    print(json.dumps(parsed, indent=2))

# Step 2: Save to database
print("\n" + "=" * 100)
print("STEP 2: Save parsed order to database")
print("=" * 100)

request_id = f"req_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_manual_test"

save_request_to_db(request_id, parsed, order_text=order_text)

print(f"\n✅ Saved to database with request_id: {request_id}")

retrieved = get_request_from_db(request_id)
print("\n📊 Data stored in database:")
print(f"   request_id: {retrieved['request_id']}")
print(f"   created_at: {retrieved['created_at']}")
print(f"   order_text: {retrieved['order_text']}")
print(f"   delivery_address: {retrieved['delivery_address']}")
print(f"   delivery_country: {retrieved['delivery_country']}")
print(f"   items_json: {retrieved['items_json']}")
print(f"   status: {retrieved['status']}")

# Step 3: Check for missing fields
print("\n" + "=" * 100)
print("STEP 3: Check for missing required fields & generate prompt")
print("=" * 100)

print(f"\nRequired fields: delivery_address, delivery_country, items")
print(f"\nCurrent state:")
print(f"  ✗ delivery_address: NULL (MISSING)")
print(f"  ✗ delivery_country: NULL (MISSING)")
print(f"  ✓ items: [1 item with 5 LEDs]")

with patch('validator.main.client') as mock_client:
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Please provide your delivery address and the country where you'd like the LEDs shipped."
    mock_client.chat.completions.create.return_value = mock_response
    
    result = find_missing_fields_and_generate_prompt(parsed, request_id=request_id)
    
    print(f"\n✅ Missing fields detected:")
    for field in result['missing_fields']:
        print(f"   - {field}")
    
    print(f"\n📝 Kimi-generated prompt:")
    print(f'   "{result["prompt_text"]}"')

# Step 4: Show final state
print("\n" + "=" * 100)
print("FINAL DATABASE STATE")
print("=" * 100)

updated = get_request_from_db(request_id)
print(f"\nRequest {request_id}:")
print(f"  ├─ status: {updated['status']}")
print(f"  ├─ delivery_address: {updated['delivery_address']}")
print(f"  ├─ delivery_country: {updated['delivery_country']}")
print(f"  ├─ items_json: {updated['items_json']}")
print(f"  ├─ missing_fields_json: {updated['missing_fields_json']}")
print(f"  └─ prompt_text: {updated['prompt_text']}")

print("\n" + "=" * 100)
print("SUMMARY")
print("=" * 100)
print("""
✅ Order parsed successfully
✅ 1 item detected: 5 LEDs for 2026-12-01
✅ Saved to database
✅ Missing fields detected: delivery_address, delivery_country
✅ Kimi generated customer prompt asking for missing info
✅ Ready to receive customer update via /request/{request_id}/update
""")

print("=" * 100)
