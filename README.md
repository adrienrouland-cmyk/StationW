# Order Parser API

A FastAPI-based validator that parses natural language order strings using the Kimi AI model and matches product names with an inventory database (CSV).

## Features

✅ **Natural Language Processing** - Understands orders written in plain English  
✅ **AI-Powered Product Matching** - Handles synonyms, abbreviations, and typos  
✅ **Structured JSON Output** - Standardized order format with product name, quantity, and date  
✅ **Multi-Product Support** - Extracts multiple products from a single order string  
✅ **Status Tracking** - Shows FOUND/NOT_FOUND status for each product  

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Kimi API Key

Copy `.env.example` to `.env` and add your Kimi API key:

```bash
cp .env.example .env
# Edit .env and set KIMI_API_KEY=your_actual_api_key
```

### 3. Prepare Your Product Database

Edit `database/stock.csv` with your actual product inventory. Format:
```csv
product_name,quantity_available,price_per_unit
Laptop,50,999.99
Desktop Computer,30,1499.99
...
```

### 4. Run the Server

```bash
python main.py
```

The API will start on `http://localhost:8000`

## Usage

### API Documentation

Visit `http://localhost:8000/docs` for interactive API documentation (Swagger UI).

### Example Request

```bash
curl -X POST "http://localhost:8000/parse-order" \
  -H "Content-Type: application/json" \
  -d '{
    "order_text": "I need 3 laptops and 5 wireless mice for delivery by next Friday. Also, can I get 2 mechanical keyboards?"
  }'
```

### Example Response

```json
{
  "orders": [
    {
      "product_name": "Laptop",
      "quantity": "3",
      "date_wanted": "2026-05-16",
      "status": "FOUND"
    },
    {
      "product_name": "Wireless Mouse",
      "quantity": "5",
      "date_wanted": "2026-05-16",
      "status": "FOUND"
    },
    {
      "product_name": "Keyboard Mechanical",
      "quantity": "2",
      "date_wanted": "MISSING",
      "status": "FOUND"
    }
  ]
}
```

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API information |
| GET | `/health` | Health check |
| POST | `/parse-order` | Parse order from natural language text |

## Error Handling

- **Empty order text** → 400 Bad Request
- **CSV file not found** → 500 Internal Server Error
- **Parsing errors** → 500 Internal Server Error with details

## How It Works

1. **Input**: Receives natural language order text
2. **Parse with Kimi**: Uses Kimi API to extract products, quantities, and dates
3. **Load Inventory**: Reads product database from CSV
4. **AI Matching**: Uses Kimi API again to intelligently match parsed product names with inventory (handles synonyms)
5. **Output**: Returns structured JSON with all matched products

## Notes

- Missing quantities default to `"MISSING"`
- Missing dates default to `"MISSING"`
- Dates are normalized to ISO format (YYYY-MM-DD)
- Products not found in inventory show status `"NOT_FOUND"`
- The AI matching is smart enough to handle variations like "laptop" vs "Laptop", "mouse" vs "wireless mouse", etc.

## License

MIT
