# Quick Start Guide

## Prerequisites

- Python 3.8+ installed
- Kimi API key (get from https://platform.moonshot.cn/)

## Step-by-Step Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Environment Variables

Copy the example file:
```bash
cp .env.example .env
```

Edit `.env` and add your Kimi API key:
```
KIMI_API_KEY=your_actual_api_key_here
KIMI_API_BASE=https://api.moonshot.cn/v1
```

### 3. Verify Product Database

Check `database/stock.csv` contains your products:
```bash
cat database/stock.csv
```

Add or modify products as needed.

### 4. Start the Server

```bash
python main.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 5. Test the API

**Option A: Using the Interactive Docs**
- Open http://localhost:8000/docs in your browser
- Find the `/parse-order` endpoint
- Click "Try it out"
- Enter a test order text
- Click "Execute"

**Option B: Using the Test Script**
In a new terminal:
```bash
python test_api.py
```

**Option C: Using curl**
```bash
curl -X POST "http://localhost:8000/parse-order" \
  -H "Content-Type: application/json" \
  -d '{
    "order_text": "I need 3 laptops and 2 wireless mice"
  }'
```

## Example Test Orders

Try these natural language orders to see how the API works:

1. **Simple order with quantity:**
   ```
   "I want to buy 5 USB-C cables"
   ```

2. **Multiple products with dates:**
   ```
   "I need 3 laptops by Friday and 10 mice for next Monday"
   ```

3. **With synonyms:**
   ```
   "Can I get a computer and a keyboard?"
   ```

4. **Non-existent product:**
   ```
   "I need 2 iPhones and 1 laptop"
   ```

## Troubleshooting

### "KIMI_API_KEY not found"
Make sure you created `.env` file and added your API key.

### "CSV file not found"
Ensure `database/stock.csv` exists in the project root.

### Connection refused on http://localhost:8000
The server might not be running. Check the terminal where you ran `python main.py`.

### API returns "NOT_FOUND" for all products
This usually means the product names don't match. Make sure your CSV has the exact product names or adjust the order text to match.

## API Response Format

All responses follow this format:

```json
{
  "orders": [
    {
      "product_name": "Laptop",
      "quantity": "3",
      "date_wanted": "2026-05-20",
      "status": "FOUND"
    }
  ]
}
```

**Fields:**
- `product_name`: The matched product name or "NOT_FOUND"
- `quantity`: The requested quantity or "MISSING" if not specified
- `date_wanted`: ISO format date (YYYY-MM-DD) or "MISSING"
- `status`: Either "FOUND" or "NOT_FOUND"

## Next Steps

- Add more products to `database/stock.csv`
- Customize prompts in `main.py` for better parsing
- Deploy to production using Docker or cloud platform
- Integrate with your ordering system

For more details, see [README.md](README.md)
