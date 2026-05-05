# Missing Fields Detection Function

## Overview
A new function `find_missing_fields_and_generate_prompt()` has been implemented to detect missing fields in parsed JSON data and generate a natural language prompt asking for those missing fields using the Kimi API.

## Function Signature
```python
def find_missing_fields_and_generate_prompt(
    json_data: dict,
    required_fields: Optional[List[str]] = None
) -> dict[str, Any]
```

## Purpose
- **Input**: Parsed JSON from order parsing (or any structured data)
- **Processing**: Identifies missing, None, empty string, or empty array fields
- **Output**: Uses Kimi to generate a friendly, professional prompt asking for missing information
- **Returns**: Dictionary with missing fields list, generated prompt text, and a boolean flag

## Return Value Structure
```python
{
    "missing_fields": List[str],      # List of field paths that are missing
    "prompt_text": str,               # Generated text from Kimi asking for missing fields
    "has_missing_fields": bool        # True if any fields are missing
}
```

## Default Required Fields
If no custom fields are provided, the function checks for:
- `client.raw_identity.phone`
- `client.raw_identity.email`
- `delivery.raw_address`
- `delivery.country`
- `payment_terms.raw`
- `items` (non-empty array)

## Usage Examples

### Basic Usage
```python
from validator.main import find_missing_fields_and_generate_prompt

json_data = {
    "client": {"raw_identity": {"phone": None, "email": "test@example.com"}},
    "delivery": {"raw_address": "123 Main St", "country": "France"},
    "payment_terms": {"raw": "Net 30"},
    "items": [{"product": "Widget", "quantity": 5}]
}

result = find_missing_fields_and_generate_prompt(json_data)
# Returns:
# {
#     "missing_fields": ["client.raw_identity.phone"],
#     "prompt_text": "Please provide your phone number.",
#     "has_missing_fields": True
# }
```

### Custom Required Fields
```python
custom_fields = ["name", "email", "phone"]
result = find_missing_fields_and_generate_prompt(json_data, custom_fields)
```

## Key Features
1. **Nested Field Detection**: Handles deeply nested JSON structures with dot notation
2. **Flexible Field Validation**: Treats None, empty strings, and empty arrays as missing
3. **Kimi Integration**: Generates professional, straight-to-the-point prompts in French/English
4. **Direct Communication**: Text generated is concise and focuses on what's needed
5. **Customizable Schema**: Supports custom required field definitions

## Kimi Configuration
- **Model**: kimi-k2.6
- **Temperature**: 0.5 (balanced between deterministic and creative)
- **Language**: Auto-detected (French or English based on context)

## Error Handling
- Raises `RuntimeError` if `KIMI_API_KEY` environment variable is not configured
- Safely handles missing parent objects and deeply nested structures

## Test Coverage
Comprehensive pytest test suite with 10 tests covering:
- ✅ No missing fields (no Kimi call needed)
- ✅ Single missing field
- ✅ Multiple missing fields
- ✅ Empty arrays
- ✅ Custom required fields
- ✅ Empty string handling
- ✅ Missing parent objects
- ✅ API configuration errors
- ✅ Kimi API call verification
- ✅ Return structure validation

## Running Tests
```bash
cd /Users/cassidy/Documents/StationW
source venv_test/bin/activate
python -m pytest tests/test_validate_order.py::TestFindMissingFields -v
```

All 10 tests pass successfully! ✅

## Integration with Order Processing
This function can be integrated into the `/parse-order` endpoint to:
1. Validate parsed order JSON
2. Identify incomplete information
3. Generate a targeted response message asking for missing data
4. Improve user experience by clearly stating what information is needed

## Requirements
- Environment variable: `KIMI_API_KEY` must be set
- Environment variable: `KIMI_API_BASE` (defaults to "https://taotoken.net/api/v1")
- Dependencies: openai (for Kimi client)
