# SQLite Database Integration

## Overview
The Order Parser API now uses SQLite to persist customer requests, enabling incremental updates and easy tracking of missing information.

## Database Schema

The `customer_requests` table stores all parsed orders with the following structure:

```sql
CREATE TABLE customer_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    order_text TEXT,
    parsed_json TEXT,
    delivery_address TEXT,
    delivery_country TEXT,
    items_json TEXT,
    missing_fields_json TEXT,
    prompt_text TEXT,
    status TEXT DEFAULT 'pending',
    notes TEXT
)
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `request_id` | TEXT | Unique identifier for the request |
| `created_at` | TEXT | ISO 8601 timestamp when created |
| `updated_at` | TEXT | ISO 8601 timestamp of last update |
| `order_text` | TEXT | Original order text (accumulated if updated) |
| `parsed_json` | TEXT | Full parsed JSON from Kimi |
| `delivery_address` | TEXT | Extracted delivery address |
| `delivery_country` | TEXT | Extracted delivery country |
| `items_json` | TEXT | JSON array of items |
| `missing_fields_json` | TEXT | JSON array of missing required fields |
| `prompt_text` | TEXT | Kimi-generated prompt asking for missing info |
| `status` | TEXT | Order status (pending, clarifying, validated, etc.) |
| `notes` | TEXT | Additional notes |

## Database File Location
```
/Users/cassidy/Documents/StationW/customer_requests.db
```

## API Endpoints

### Parse Order and Save to Database
```
POST /parse-order
Content-Type: application/json

{
  "order_text": "I need 10 bearings model 6204-2RS, shipped to Paris France"
}
```

**Response:**
- Saves parsed order to database
- Returns `OrderResponse` with additional `missing_fields` and `prompt_text` if fields are missing
- Example with missing fields:
```json
{
  "meta": { ... },
  "client": { ... },
  "order_status": "clarifying",
  "order_lines": [ ... ],
  "missing_fields": ["delivery.raw_address", "delivery.country"],
  "prompt_text": "We need your delivery address and country to process this order."
}
```

### Retrieve Request
```
GET /request/{request_id}
```

**Response:**
```json
{
  "id": 1,
  "request_id": "req_20260505_120000_abc123",
  "created_at": "2026-05-05T12:00:00Z",
  "updated_at": "2026-05-05T12:00:00Z",
  "order_text": "I need 10 bearings...",
  "parsed_json": { ... },
  "delivery_address": "123 Main St",
  "delivery_country": "France",
  "items_json": [ ... ],
  "missing_fields_json": [],
  "prompt_text": "",
  "status": "pending",
  "notes": null
}
```

### Find Requests with Missing Fields
```
GET /requests/missing?limit=10
```

**Response:**
```json
{
  "total": 3,
  "requests": [
    {
      "request_id": "req_...",
      "created_at": "...",
      "updated_at": "...",
      "missing_fields_json": ["delivery.raw_address", "items"],
      "prompt_text": "We need your delivery address and product items...",
      "status": "pending"
    },
    ...
  ]
}
```

### Update Request with New Order Text
```
POST /request/{request_id}/update
Content-Type: application/json

{
  "new_order_text": "Additional info: ship to 123 Main St, Paris"
}
```

**Behavior:**
- Parses new order text with Kimi
- Appends to original order text (creates history)
- Re-checks for missing fields
- Updates database with new information
- Returns updated request with new missing fields analysis

**Response:**
```json
{
  "request": { ... },
  "missing_fields": ["items"],
  "prompt_text": "Please provide the product items..."
}
```

### Update Specific Field
```
POST /request/{request_id}/field
Content-Type: application/json

{
  "field_name": "delivery_address",
  "value": "123 Main Street, Paris, 75001"
}
```

**Valid Fields:**
- `delivery_address`
- `delivery_country`
- `items_json` (JSON array as string)
- `missing_fields_json` (JSON array as string)
- `prompt_text`
- `status`
- `notes`
- `parsed_json` (full JSON as string)

## Python Functions

### Save Request
```python
from validator.main import save_request_to_db

save_request_to_db(
    request_id="req_...",
    parsed_json={...},
    order_text="...",
    missing_fields=["delivery.raw_address"],
    prompt_text="..."
)
```

### Retrieve Request
```python
from validator.main import get_request_from_db

request = get_request_from_db("req_...")
```

### Find Missing Fields Requests
```python
from validator.main import find_requests_with_missing_fields

requests = find_requests_with_missing_fields(limit=10)
```

### Update Field
```python
from validator.main import update_request_field

update_request_field(
    request_id="req_...",
    field_name="delivery_address",
    field_value="123 Main St"
)
```

### Check for Missing Fields
```python
from validator.main import find_missing_fields_and_generate_prompt

result = find_missing_fields_and_generate_prompt(
    json_data={...},
    request_id="req_..."  # Optional: saves to database
)
# Returns:
# {
#   "missing_fields": ["delivery.raw_address"],
#   "prompt_text": "Please provide...",
#   "has_missing_fields": True
# }
```

## Workflow Example

### 1. Initial Order Parse
```bash
curl -X POST http://localhost:8000/parse-order \
  -H "Content-Type: application/json" \
  -d '{
    "order_text": "I want 5 hydraulic filters, need them ASAP"
  }'
```

**Response includes:**
- `request_id`: "req_20260505_120000_abc123"
- `missing_fields`: ["delivery.raw_address", "delivery.country"]
- `prompt_text`: "Please provide your delivery address and country."

### 2. Customer Provides More Info
```bash
curl -X POST http://localhost:8000/request/req_20260505_120000_abc123/update \
  -H "Content-Type: application/json" \
  -d '{
    "new_order_text": "Ship to 42 Rue de Paris, Paris, France"
  }'
```

**Response:**
- Updated request
- `missing_fields`: [] (now complete!)
- Order ready for processing

### 3. Query All Incomplete Orders
```bash
curl http://localhost:8000/requests/missing?limit=20
```

Returns all requests still waiting for customer information.

## Benefits

✅ **Persistence** - Orders survive application restarts  
✅ **Incremental Updates** - Customers can provide info in multiple messages  
✅ **Easy Queries** - Find all incomplete orders with one call  
✅ **Audit Trail** - Track when each field was added  
✅ **No External DB** - SQLite included with Python  
✅ **Transaction Safety** - ACID guarantees for data integrity  
✅ **Flexible Schema** - Easy to add new fields  

## Database Backup

To backup the database:
```bash
cp /Users/cassidy/Documents/StationW/customer_requests.db \
   /Users/cassidy/Documents/StationW/customer_requests.backup.$(date +%Y%m%d_%H%M%S).db
```

## Resetting the Database

To clear all requests (development only):
```bash
rm /Users/cassidy/Documents/StationW/customer_requests.db
# Database will be recreated on next application start
```

## Logging

Database operations are logged at INFO level. Check logs for:
- Request saves: `"Request {id} saved to database"`
- Field updates: `"Updated {field} for request {id}"`
- Missing fields: `"Generated Kimi message: ..."`

Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```
