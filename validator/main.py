"""
FastAPI Validator for Order Parsing
Parses natural language order strings using Kimi API and matches products with inventory
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime, timezone
import csv
import os
from dotenv import load_dotenv
from openai import OpenAI
import json

# Load environment variables
load_dotenv()

app = FastAPI(
    title="Order Parser API",
    description="Parse natural language orders and match them with product inventory",
    version="1.0.0"
)

# Initialize Kimi client
KIMI_API_KEY = os.getenv("KIMI_API_KEY")
KIMI_API_BASE = os.getenv("KIMI_API_BASE", "https://taotoken.net/api/v1")

# Initialize Kimi client if key is present; otherwise keep None so module import
# doesn't fail. Endpoint will return a clear error if client is missing.
if KIMI_API_KEY:
    client = OpenAI(
        api_key=KIMI_API_KEY,
        base_url=KIMI_API_BASE
    )
else:
    client = None

# Pydantic models
class MetaInfo(BaseModel):
    request_id: str
    created_at: str
    intake_channel: str
    language: str


class RawIdentity(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None


class ClientInfo(BaseModel):
    raw_identity: RawIdentity


class ProductNormalized(BaseModel):
    sku: Optional[str] = None


class ProductInfo(BaseModel):
    raw_description: str
    normalized: ProductNormalized
    status: str


class PricingInfo(BaseModel):
    wanted_unit_price: Optional[float] = None
    stock_unit_price: Optional[float] = None


class OrderLine(BaseModel):
    line_id: int
    product: ProductInfo
    quantity: Optional[int] = None
    date_wanted: Optional[str] = None
    pricing: PricingInfo


class DeliveryInfo(BaseModel):
    raw_address: Optional[str] = None
    country: Optional[str] = None
    pick_up: Optional[bool] = None


class PaymentTermsInfo(BaseModel):
    raw: Optional[str] = None
    type: Optional[str] = None
    details: Optional[str] = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: MetaInfo = Field(alias="_meta")
    client: ClientInfo
    order_status: str
    order_lines: List[OrderLine]
    delivery: Optional[DeliveryInfo] = None
    payment_terms: Optional[PaymentTermsInfo] = None


class OrderRequest(BaseModel):
    order_text: str
    client_id: Optional[str] = None

# CSV utilities
def load_products() -> dict:
    """Load products from CSV file"""
    products = {}
    csv_path = "database/stock.csv"
    
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found at {csv_path}")
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            product_name = row['product_name'].strip()
            # normalize numeric fields
            try:
                quantity_available = int(row.get('quantity_available') or 0)
            except ValueError:
                quantity_available = 0
            try:
                price_per_unit = float(row.get('price_per_unit') or 0.0)
            except ValueError:
                price_per_unit = 0.0

            products[product_name] = {
                'name': product_name,
                'quantity_available': quantity_available,
                'price_per_unit': price_per_unit
            }
            # include optional description and sku_code if present
            desc = (row.get('description') or '').strip()
            sku = (row.get('sku_code') or '').strip()
            if desc:
                products[product_name]['description'] = desc
            else:
                products[product_name]['description'] = ''
            if sku:
                products[product_name]['sku_code'] = sku
            else:
                products[product_name]['sku_code'] = ''
    
    return products

# AI parsing function
def parse_order_with_kimi(order_text: str) -> List[dict]:
    """
    Use Kimi API to parse natural language order text
    Returns list of dicts with product, quantity, and date
    """
    if client is None:
        raise RuntimeError("KIMI_API_KEY not configured. Set KIMI_API_KEY in environment.")

    # Ask Kimi to extract all order fields that feed the response envelope.
    prompt = (
        "Extract ALL order information from the following text and return ONLY valid JSON.\n"
        "The text may be in French or English. Preserve raw wording when possible and infer structured values when obvious.\n"
        "Use this structure exactly:\n"
        "{\n"
        '  "client": {"raw_identity": {"phone": null, "email": null}},\n'
        '  "delivery": {"raw_address": null, "country": null, "pick_up": null},\n'
        '  "payment_terms": {"raw": null, "type": null, "details": null},\n'
        '  "items": [\n'
        '    {"product": <string or null>, "quantity": <number or null>, "date": <ISO date or null>, "wanted_price": <number or null>}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Extract every product line or requested item, even if the text contains many items.\n"
        "- Keep one object per line item in the items array.\n"
        "- Fill client.raw_identity.phone if a phone number is present. Fill client.raw_identity.email if an email is present.\n"
        "- Put the full delivery address in delivery.raw_address when present. Set delivery.country when it can be inferred.\n"
        "- Set delivery.pick_up to true only if the text explicitly asks for pickup or collection.\n"
        "- Put the raw payment phrase in payment_terms.raw, classify payment_terms.type when possible (for example net_30, prepaid, cash_on_delivery, bank_transfer, card, other), and put a short explanation in payment_terms.details.\n"
        "- For items, use the exact product wording from the source as much as possible.\n"
        "- For quantity, return a number when possible.\n"
        "- For wanted_price, return the unit price if the text states one, otherwise null.\n"
        "- For date, return ISO-8601 when you can infer it; otherwise null.\n"
        f"Order text: {order_text}\n\n"
        "Return ONLY valid JSON object, no other text."
)

    response = client.chat.completions.create(
        model="kimi-k2.6",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.25,
    )

    try:
        result = response.choices[0].message.content.strip()
        # Extract JSON object from response
        json_start = result.find('{')
        json_end = result.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            json_str = result[json_start:json_end]
            parsed = json.loads(json_str)
            return parsed
        else:
            return {
                "client": {"raw_identity": {"phone": None, "email": None}},
                "delivery": {"raw_address": None, "country": None, "pick_up": None},
                "payment_terms": {"raw": None, "type": None, "details": None},
                "items": [],
            }
    except (json.JSONDecodeError, IndexError) as e:
        print(f"Error parsing Kimi response: {e}")
        return {
            "client": {"raw_identity": {"phone": None, "email": None}},
            "delivery": {"raw_address": None, "country": None, "pick_up": None},
            "payment_terms": {"raw": None, "type": None, "details": None},
            "items": [],
        }

def match_product_with_ai(product_name: str, available_products: dict) -> Optional[str]:
    """
    Use Kimi API to find the best matching product from inventory
    Handles synonyms and typos
    """
    
    # build a richer products list with descriptions and sku to help matching
    product_objs = []
    for p_name, meta in available_products.items():
        product_objs.append({
            'name': p_name,
            'description': meta.get('description', ''),
            'sku_code': meta.get('sku_code', '')
        })

    prompt = f"""Given a product name and a list of available products (with descriptions and SKU codes), find the best matching product.
Handle synonyms, abbreviations, and slight typos. Prefer matches using the description when the name is ambiguous.

Product to match: "{product_name}"
Available products: {json.dumps(product_objs)}

Return ONLY the exact product name from the list that best matches, or "NOT_FOUND" if no reasonable match exists.
Do not return anything else, just the product name or NOT_FOUND."""

    if client is None:
        # fallback to simple exact match when client not configured
        if product_name in available_products:
            return product_name
        # naive lower-case partial match
        lower = product_name.lower()
        for p, meta in available_products.items():
            # check name
            if lower in p.lower() or p.lower() in lower:
                return p
            # check description keywords
            desc = (meta.get('description') or '').lower()
            if desc and (lower in desc or any(word in desc for word in lower.split())):
                return p
        return "NOT_FOUND"

    response = client.chat.completions.create(
        model="kimi-k2.6",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )

    matched_product = response.choices[0].message.content.strip().strip('"')

    if matched_product == "NOT_FOUND" or matched_product not in available_products:
        return "NOT_FOUND"
    return matched_product

# Main endpoint
@app.post("/parse-order", response_model=OrderResponse)
async def parse_order(request: OrderRequest) -> OrderResponse:
    """
    Parse a natural language order string and match products with inventory
    
    Input: order_text (string with order information in natural language)
    Output: JSON with matched products, quantities, and dates wanted
    """
    
    if not request.order_text or request.order_text.strip() == "":
        raise HTTPException(status_code=400, detail="order_text cannot be empty")
    
    try:
        # Load available products
        products = load_products()
        
        # Parse order using Kimi (returns dict with client_id and items)
        parsed = parse_order_with_kimi(request.order_text)
        parsed_items = parsed.get('items', []) if isinstance(parsed, dict) else []

        order_lines = []

        for index, item in enumerate(parsed_items, start=1):
            product_name_raw = (item.get('product') or '').strip()
            quantity = item.get('quantity')
            date_wanted = item.get('date')
            wanted_price = item.get('wanted_price')

            try:
                quantity_int = int(quantity) if quantity is not None else None
            except Exception:
                quantity_int = None

            try:
                wanted_price_val = float(wanted_price) if wanted_price is not None else None
            except Exception:
                wanted_price_val = None

            matched_product = match_product_with_ai(product_name_raw, products)

            if matched_product != "NOT_FOUND":
                sku_out = products[matched_product].get('sku_code') or None
                stock_unit_price = products[matched_product].get('price_per_unit')
            else:
                sku_out = None
                stock_unit_price = None

            order_lines.append(
                OrderLine(
                    line_id=index,
                    product=ProductInfo(
                        raw_description=product_name_raw or "MISSING",
                        normalized=ProductNormalized(sku=sku_out),
                        status="FOUND" if matched_product != "NOT_FOUND" else "NOT_FOUND",
                    ),
                    quantity=quantity_int,
                    date_wanted=date_wanted if date_wanted else None,
                    pricing=PricingInfo(
                        wanted_unit_price=wanted_price_val,
                        stock_unit_price=stock_unit_price,
                    ),
                )
            )

        if not order_lines:
            raise HTTPException(status_code=400, detail="Could not parse any products from the order text")

        parsed_client = parsed.get('client', {}) if isinstance(parsed, dict) else {}
        parsed_identity = parsed_client.get('raw_identity', {}) if isinstance(parsed_client, dict) else {}
        raw_phone = parsed_identity.get('phone') or request.client_id
        raw_email = parsed_identity.get('email')

        parsed_delivery = parsed.get('delivery', {}) if isinstance(parsed, dict) else {}
        parsed_payment_terms = parsed.get('payment_terms', {}) if isinstance(parsed, dict) else {}

        # Determine order_status: if any NOT_FOUND -> clarifying, else pending_payement
        order_status = "pending_payement"
        for line in order_lines:
            if line.product.status == "NOT_FOUND":
                order_status = "clarifying"
                break

        request_id = f"req_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{os.urandom(3).hex()}"
        created_at = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

        return OrderResponse(
            meta=MetaInfo(
                request_id=request_id,
                created_at=created_at,
                intake_channel="whatsapp_voice",
                language="fr",
            ),
            client=ClientInfo(
                raw_identity=RawIdentity(
                    phone=raw_phone,
                    email=raw_email,
                )
            ),
            order_status=order_status,
            order_lines=order_lines,
            delivery=DeliveryInfo(
                raw_address=parsed_delivery.get('raw_address') if isinstance(parsed_delivery, dict) else None,
                country=parsed_delivery.get('country') if isinstance(parsed_delivery, dict) else None,
                pick_up=parsed_delivery.get('pick_up') if isinstance(parsed_delivery, dict) else None,
            ),
            payment_terms=PaymentTermsInfo(
                raw=parsed_payment_terms.get('raw') if isinstance(parsed_payment_terms, dict) else None,
                type=parsed_payment_terms.get('type') if isinstance(parsed_payment_terms, dict) else None,
                details=parsed_payment_terms.get('details') if isinstance(parsed_payment_terms, dict) else None,
            ),
        )
    
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing order: {str(e)}")

# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

# Root endpoint
@app.get("/")
async def root():
    """API information"""
    return {
        "name": "Order Parser API",
        "version": "1.0.0",
        "endpoints": {
            "POST /parse-order": "Parse a natural language order",
            "GET /health": "Health check",
            "GET /docs": "API documentation"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
