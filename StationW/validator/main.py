"""
FastAPI Validator for Order Parsing
Parses natural language order strings using Kimi API and matches products with inventory
Includes quote generation functionality
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import csv
import os
import base64
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import json
import yaml
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
import sqlite3
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize base directory for quote functionality
BASE_DIR = Path(__file__).resolve().parent.parent / "quote"
CONFIG_PATH = BASE_DIR / "config.yaml"
MONEY_QUANT = Decimal("0.01")

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

app = FastAPI(
    title="Order Parser API",
    description="Parse natural language orders and match them with product inventory. Includes quote generation.",
    version="1.0.0"
)

# Add CORS middleware to allow web app to make requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import RedirectResponse

# Mount Dashboard Static Files
dashboard_path = Path(__file__).resolve().parent / "dashboard"
dashboard_path.mkdir(parents=True, exist_ok=True)

@app.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse(url="/dashboard/index.html")

app.mount("/dashboard", StaticFiles(directory=dashboard_path, html=True), name="dashboard")

# ==================== Database Setup ====================

DB_PATH = Path(__file__).resolve().parent.parent / "customer_requests.db"


def init_db():
    """Initialize SQLite database schema"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create customer_requests table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_requests (
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
    """)
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def save_request_to_db(request_id: str, parsed_json: dict, order_text: str = "", missing_fields: List[str] = None, prompt_text: str = "") -> None:
    """Save or update a parsed request in the database"""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    
    # Extract fields from parsed JSON
    delivery = parsed_json.get("delivery", {})
    delivery_address = delivery.get("raw_address") if isinstance(delivery, dict) else None
    delivery_country = delivery.get("country") if isinstance(delivery, dict) else None
    items = parsed_json.get("items", [])
    
    try:
        cursor.execute("""
            INSERT INTO customer_requests 
            (request_id, created_at, updated_at, order_text, parsed_json, delivery_address, 
             delivery_country, items_json, missing_fields_json, prompt_text, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(request_id) DO UPDATE SET
                updated_at = ?,
                order_text = ?,
                parsed_json = ?,
                delivery_address = ?,
                delivery_country = ?,
                items_json = ?,
                missing_fields_json = ?,
                prompt_text = ?
        """, (
            request_id, now, now, order_text, json.dumps(parsed_json),
            delivery_address, delivery_country, json.dumps(items),
            json.dumps(missing_fields or []), prompt_text,
            # Values for UPDATE
            now, order_text, json.dumps(parsed_json),
            delivery_address, delivery_country, json.dumps(items),
            json.dumps(missing_fields or []), prompt_text
        ))
        conn.commit()
        logger.info(f"Request {request_id} saved to database")
    except Exception as e:
        logger.error(f"Error saving request to database: {e}")
    finally:
        conn.close()


def get_request_from_db(request_id: str) -> Optional[dict]:
    """Retrieve a request from the database"""
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM customer_requests WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def find_requests_with_missing_fields(limit: int = 10) -> List[dict]:
    """Find all requests with missing required fields"""
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM customer_requests 
            WHERE (delivery_address IS NULL OR delivery_address = '')
               OR (delivery_country IS NULL OR delivery_country = '')
               OR (items_json = '[]' OR items_json IS NULL)
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_request_field(request_id: str, field_name: str, field_value: Any) -> None:
    """Update a specific field in a customer request"""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    
    valid_fields = [
        'delivery_address', 'delivery_country', 'items_json',
        'missing_fields_json', 'prompt_text', 'status', 'notes', 'parsed_json'
    ]
    
    if field_name not in valid_fields:
        raise ValueError(f"Invalid field: {field_name}")
    
    try:
        if field_name in ['items_json', 'missing_fields_json', 'parsed_json']:
            field_value = json.dumps(field_value) if not isinstance(field_value, str) else field_value
        
        cursor.execute(f"""
            UPDATE customer_requests 
            SET {field_name} = ?, updated_at = ?
            WHERE request_id = ?
        """, (field_value, now, request_id))
        
        conn.commit()
        logger.info(f"Updated {field_name} for request {request_id}")
    finally:
        conn.close()


# Initialize database on startup
init_db()

# ==================== Quote Engine Helper Functions ====================

def resolve_path(path_value: str) -> str:
    """Resolve relative paths from the quote directory"""
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str(BASE_DIR / path)


def load_config(config_path: str = None) -> dict:
    """Load configuration from YAML file"""
    if config_path is None:
        config_path = str(CONFIG_PATH)
    with Path(config_path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _clean_value(value: Any, default: Any = "") -> Any:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    return value


def _clean_str(value: Any) -> str:
    value = _clean_value(value, "")
    return str(value).strip() if value != "" else ""


def _clean_int(value: Any) -> int:
    value = _clean_value(value, 0)
    if value == "":
        return 0
    return int(float(value))


def _clean_decimal(value: Any) -> Decimal:
    value = _clean_value(value, 0)
    if value == "":
        return Decimal("0")
    return Decimal(str(value))


def _clean_bool(value: Any) -> bool:
    value = _clean_value(value, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "1", "yes", "y", "oui", "vrai"}


def _format_date(value: Any) -> str:
    value = _clean_value(value, "")
    if value == "":
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return _clean_str(value)
    return parsed.strftime("%d/%m/%Y")


def _format_money(value: Decimal) -> str:
    return f"{value:,.2f} €"


def _safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: _clean_value(value, "") for key, value in row.items()}
        for row in df.to_dict(orient="records")
    ]


def _normalize_orders(orders: pd.DataFrame) -> pd.DataFrame:
    column_aliases = {
        "sku_code": "sku",
        "quantity": "qty",
        "unit_price": "unit_price_eur",
        "total_price": "total_price_eur",
        "delivery_adress": "delivery_address",
    }
    orders = orders.rename(
        columns={
            source: target
            for source, target in column_aliases.items()
            if source in orders.columns and target not in orders.columns
        }
    )

    required_defaults = {
        "order_id": "",
        "request_id": "",
        "client_id": "",
        "company_name": "",
        "order_date": "",
        "channel": "",
        "sku": "",
        "product_name": "",
        "qty": 0,
        "unit_price_eur": 0,
        "express": False,
        "delivery_address": "",
        "delivery_date": "",
        "status": "",
        "invoice_id": "",
        "paid": False,
        "payment_date": "",
        "agent_decision": "",
        "notes": "",
    }
    for column, default in required_defaults.items():
        if column not in orders.columns:
            orders[column] = default

    return orders


def _empty_clients() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "client_id",
            "client_type",
            "onboarding_status",
            "company_name",
            "contact_name",
            "phone",
            "email",
            "address",
            "city",
            "siret",
            "framework_contract_id",
            "vip_tier",
            "credit_limit_eur",
            "outstanding_balance_eur",
            "days_overdue",
            "reliability_score",
            "total_orders_12m",
            "total_revenue_12m_eur",
            "notes",
        ]
    )


def _empty_products() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "sku",
            "product_name",
            "brand",
            "category",
            "unit",
            "catalogue_price_eur",
            "stock_qty",
            "moq",
            "lead_time_days",
            "description_specs",
            "status",
        ]
    )


def load_data(excel_path: str) -> dict[str, pd.DataFrame]:
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    if path.suffix.lower() == ".csv":
        orders = pd.read_csv(path, sep=None, engine="python", dtype=str)
        clients = _empty_clients()
        products = _empty_products()
    else:
        orders = pd.read_excel(
            path,
            sheet_name="Order History",
            dtype={"order_id": str, "client_id": str, "sku": str},
        )
        clients = pd.read_excel(path, sheet_name="Clients", dtype={"client_id": str})
        products = pd.read_excel(path, sheet_name="Products", dtype={"sku": str})

    orders = _normalize_orders(orders)

    for frame, columns in (
        (orders, ["order_id", "client_id", "sku"]),
        (clients, ["client_id"]),
        (products, ["sku"]),
    ):
        for column in columns:
            if column in frame.columns:
                frame[column] = frame[column].astype("string").fillna("").str.strip()

    return {"orders": orders, "clients": clients, "products": products}


def get_order(data: dict[str, pd.DataFrame], order_id: str) -> dict[str, Any]:
    orders = data["orders"].copy()
    clients = data["clients"].copy()
    products = data["products"].copy()

    order_lines = orders[orders["order_id"].astype(str) == str(order_id)].copy()
    if order_lines.empty:
        raise ValueError(f"Order not found: {order_id}")

    client_id = _clean_str(order_lines.iloc[0].get("client_id", ""))
    client_rows = clients[clients["client_id"].astype(str) == client_id]
    client = _safe_records(client_rows.head(1))[0] if not client_rows.empty else {}
    if not client:
        first_line = order_lines.iloc[0]
        client = {
            "client_id": client_id,
            "company_name": _clean_str(first_line.get("company_name", "")),
            "contact_name": "",
            "phone": "",
            "email": "",
            "address": _clean_str(first_line.get("delivery_address", "")),
            "city": "",
            "siret": "",
            "vip_tier": "",
        }

    product_cols = ["sku", "unit", "description_specs"]
    available_product_cols = [col for col in product_cols if col in products.columns]
    enriched = order_lines.merge(
        products[available_product_cols],
        on="sku",
        how="left",
        suffixes=("", "_product"),
    )

    lines: list[dict[str, Any]] = []
    for index, row in enriched.reset_index(drop=True).iterrows():
        qty = _clean_int(row.get("qty", 0))
        unit_price = _clean_decimal(row.get("unit_price_eur", 0))
        line_total = qty * unit_price

        lines.append(
            {
                "line_id": index + 1,
                "sku": _clean_str(row.get("sku", "")),
                "product_name": _clean_str(row.get("product_name", "")),
                "description_specs": _clean_str(row.get("description_specs", "")),
                "qty": qty,
                "unit": _clean_str(row.get("unit", "")),
                "unit_price_eur": unit_price,
                "line_total_ht": line_total,
                "unit_price_eur_fmt": _format_money(unit_price),
                "line_total_ht_fmt": _format_money(line_total),
                "express": False,
            }
        )

    first_line = order_lines.iloc[0]
    return {
        "order_id": str(order_id),
        "order_date": _format_date(first_line.get("order_date", "")),
        "client": client,
        "lines": lines,
        "has_express": False,
        "delivery_address": _clean_str(first_line.get("delivery_address", "")),
        "delivery_date": _format_date(first_line.get("delivery_date", "")),
        "agent_decision": _clean_str(first_line.get("agent_decision", "")),
    }


def calculate_totals(order: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    total_ht = sum(
        (Decimal(line["line_total_ht"]) for line in order["lines"]), Decimal("0")
    )
    express_fee = Decimal("0")
    total_ht_with_fees = total_ht + express_fee
    tva_rate = Decimal(str(config["quote"]["tva_rate"]))
    tva_amount = (total_ht_with_fees * tva_rate).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    total_ttc = (total_ht_with_fees + tva_amount).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )

    total_ht = total_ht.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    express_fee = express_fee.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    total_ht_with_fees = total_ht_with_fees.quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )

    return {
        "total_ht": total_ht,
        "express_fee": express_fee,
        "total_ht_with_fees": total_ht_with_fees,
        "tva_rate": tva_rate,
        "tva_amount": tva_amount,
        "total_ttc": total_ttc,
        "total_ht_fmt": _format_money(total_ht),
        "express_fee_fmt": _format_money(express_fee),
        "total_ht_with_fees_fmt": _format_money(total_ht_with_fees),
        "tva_amount_fmt": _format_money(tva_amount),
        "total_ttc_fmt": _format_money(total_ttc),
        "tva_rate_fmt": f"{(tva_rate * Decimal('100')).quantize(Decimal('1'))}%",
    }


def get_next_quote_number(export_path: str) -> tuple[int, int]:
    path = Path(export_path)
    if not path.exists():
        return 1, datetime.now().year

    max_quote_number = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                max_quote_number = max(
                    max_quote_number, int(row.get("quote_number", "0") or "0")
                )
            except ValueError:
                continue

    return max_quote_number + 1, datetime.now().year


def append_quote_export(
    export_path: str,
    order_id: str,
    quote_number: int,
    quote_year: int,
    pdf_path: Path,
    totals: dict[str, Any],
) -> None:
    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "created_at",
        "quote_number",
        "quote_year",
        "order_id",
        "pdf_filename",
        "pdf_path",
        "total_ht_eur",
        "total_ttc_eur",
    ]
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "quote_number": quote_number,
                "quote_year": quote_year,
                "order_id": order_id,
                "pdf_filename": pdf_path.name,
                "pdf_path": str(pdf_path),
                "total_ht_eur": totals["total_ht"],
                "total_ttc_eur": totals["total_ttc"],
            }
        )


def load_logo_base64(logo_path: str) -> str | None:
    path = Path(logo_path)
    if not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_pdf(html: str) -> bytes:
    homebrew_lib_dirs = ["/opt/homebrew/lib", "/usr/local/lib"]
    existing_fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    fallback_parts = [part for part in existing_fallback.split(":") if part]
    for lib_dir in homebrew_lib_dirs:
        if Path(lib_dir).exists() and lib_dir not in fallback_parts:
            fallback_parts.append(lib_dir)
    if fallback_parts:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(fallback_parts)

    from weasyprint import HTML

    return HTML(string=html, base_url=".").write_pdf()


def generate_quote_pdf(
    order_id: str,
    excel_path: str,
    config: dict[str, Any],
    export_path: str,
    template_path: str,
    logo_path: str,
) -> tuple[bytes, int]:
    data = load_data(excel_path)
    order = get_order(data, order_id)
    totals = calculate_totals(order, config)
    quote_number, quote_year = get_next_quote_number(export_path)

    template_file = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(template_file.parent)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template(template_file.name)

    emission = date.today()
    validity = emission + timedelta(days=int(config["quote"]["validity_days"]))
    logo_b64 = load_logo_base64(logo_path)

    html = template.render(
        supplier=config["supplier"],
        client=order["client"],
        order=order,
        lines=order["lines"],
        totals=totals,
        quote_number=quote_number,
        quote_year=quote_year,
        emission_date=emission.strftime("%d/%m/%Y"),
        validity_date=validity.strftime("%d/%m/%Y"),
        logo_b64=logo_b64,
        config=config,
        has_express=order["has_express"],
        express_fee=totals["express_fee"],
    )
    pdf_bytes = render_pdf(html)

    output_dir = Path(template_path).resolve().parents[1] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_order_id = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in str(order_id)
    )
    pdf_path = output_dir / f"quote_{quote_year}_{quote_number:05d}_{safe_order_id}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    append_quote_export(export_path, order_id, quote_number, quote_year, pdf_path, totals)

    return pdf_bytes, quote_number
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
    client_score: Optional[float] = 0.5  # Between 0 (no discount) and 1 (max 20% discount)


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
    meta: MetaInfo
    client: ClientInfo
    order_status: str
    order_lines: List[OrderLine]
    delivery: Optional[DeliveryInfo] = None
    payment_terms: Optional[PaymentTermsInfo] = None


class OrderRequest(BaseModel):
    order_text: str
    client_id: Optional[str] = None


class UpdateRequest(BaseModel):
    new_order_text: str

# CSV utilities
def load_products() -> dict:
    """Load products from CSV file"""
    products = {}
    # Resolve against the repository root so the server works no matter where it is launched from.
    repo_root = Path(__file__).resolve().parent.parent
    candidate_paths = [
        repo_root / "database" / "stock.csv",
        repo_root / "database" / "products.csv",
    ]
    csv_path = next((path for path in candidate_paths if path.exists()), None)

    if not csv_path:
        raise FileNotFoundError(f"CSV file not found in database/ (tried stock.csv and products.csv)")

    # detect delimiter from first line
    with csv_path.open('r', encoding='utf-8') as f:
        first = f.readline()
        delimiter = ';' if ';' in first and first.count(';') > first.count(',') else ','
        f.seek(0)
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            product_name = row['product_name'].strip()
            # normalize numeric fields
            # tolerate non-breaking spaces in numbers
            def parse_int(x):
                if x is None:
                    return 0
                try:
                    return int(str(x).replace('\u202f','').replace('\xa0','').replace(' ',''))
                except Exception:
                    try:
                        return int(float(str(x)))
                    except Exception:
                        return 0

            def parse_float(x):
                if x is None:
                    return 0.0
                try:
                    return float(str(x).replace('\u202f','').replace('\xa0','').replace(' ', '').replace(',','.'))
                except Exception:
                    return 0.0

            quantity_available = parse_int(row.get('quantity_available') or row.get('quantity available') or row.get('quantity') )
            price_per_unit = parse_float(row.get('price_per_unit') or row.get('price per unit') or row.get('price'))

            products[product_name] = {
                'name': product_name,
                'quantity_available': quantity_available,
                'price_per_unit': price_per_unit
            }
            # include optional description and sku_code if present
            desc = (row.get('description') or '').strip()
            sku = (row.get('sku_code') or row.get('sku') or '').strip()
            if desc:
                products[product_name]['description'] = desc
            else:
                products[product_name]['description'] = ''
            if sku:
                products[product_name]['sku_code'] = sku
            else:
                products[product_name]['sku_code'] = ''

    return products


# Validation endpoint
@app.post("/validate-order")
async def validate_order(order: OrderResponse):
    """Validate a fully-populated order JSON.

    Returns one of:
    - Validated: every line has requested quantity available AND stock price >= wanted price
    - Negotiating: at least one line has quantity shortfall or stock price < wanted price. Returns `next_step` proposals.
    - CANCELED: input is empty or all lines have product.status == "NOT_FOUND"
    """

    # Cancelled: empty or all NOT_FOUND
    if not order.order_lines or all((line.product.status or "").upper() == "NOT_FOUND" for line in order.order_lines):
        return {"result": "CANCELED", "reason": "empty_or_all_not_found"}

    try:
        products = load_products()
    except FileNotFoundError:
        # If inventory file missing, fall back to using provided pricing only
        products = {}

    negotiating_items = []
    client_score = order.client.client_score if order.client else 0.5
    client_score = max(0.0, min(1.0, client_score))  # Clamp to [0, 1]

    for line in order.order_lines:
        line_id = line.line_id
        raw = line.product.raw_description
        sku = (line.product.normalized.sku or "") if line.product and line.product.normalized else ""
        requested_qty = line.quantity or 0
        wanted_price = line.pricing.wanted_unit_price if line.pricing else None
        stock_price = line.pricing.stock_unit_price if line.pricing else None

        # Try to get authoritative inventory info when SKU or product name matches
        inv_qty = None
        inv_price = None
        if sku and products:
            # find by sku_code
            for pmeta in products.values():
                if (pmeta.get('sku_code') or "") == sku:
                    inv_qty = pmeta.get('quantity_available')
                    inv_price = pmeta.get('price_per_unit')
                    break
        if inv_qty is None and products:
            # try by exact product name
            if raw in products:
                inv_qty = products[raw].get('quantity_available')
                inv_price = products[raw].get('price_per_unit')
            else:
                # try case-insensitive match
                for pname, pmeta in products.items():
                    if raw and raw.lower() in pname.lower():
                        inv_qty = pmeta.get('quantity_available')
                        inv_price = pmeta.get('price_per_unit')
                        break

        # Decide if this line is acceptable
        qty_issue = False
        price_issue = False
        offered_qty = requested_qty
        offered_price = wanted_price

        # Check quantity issue
        if inv_qty is not None and requested_qty > inv_qty:
            qty_issue = True
            offered_qty = inv_qty  # Offer available quantity

        # Check price issue and calculate discount-adjusted price
        authoritative_price = inv_price if inv_price is not None else stock_price

        if authoritative_price is not None and wanted_price is not None:
            # Price issue: client asks a price lower than our stock price
            if wanted_price < authoritative_price:
                price_issue = True
                # Calculate discount based on client_score
                # Discount ranges from 0% (score=0) to 20% (score=1)
                max_discount = 0.2
                discount = max_discount * client_score
                # Minimum price we accept = stock_price * (1 - discount)
                min_acceptable_price = authoritative_price * (1 - discount)
                # Offer price: max between what client wants and our minimum acceptable
                offered_price = max(wanted_price, min_acceptable_price)

        if qty_issue or price_issue:
            negotiating_items.append({
                "line_id": line_id,
                "product": raw,
                "requested_quantity": requested_qty,
                "available_quantity": inv_qty,
                "wanted_unit_price": wanted_price,
                "stock_unit_price": authoritative_price,
                "client_score": client_score,
                "proposal": {
                    "offer_quantity": offered_qty,
                    "offer_unit_price": offered_price,
                }
            })

    if not negotiating_items:
        # All good
        return {"result": "VALIDATED"}

    # Negotiation required
    return {
        "result": "NEGOTIATING",
        "next_step": {
            "proposals": negotiating_items
        }
    }

# AI parsing function
def parse_order_with_kimi(order_text: str, current_state: Optional[dict] = None) -> dict:
    """
    Use Kimi API to parse natural language order text.
    If current_state is provided, it merges the new information into the existing state.
    Returns dict with structured order data.
    """
    if client is None:
        raise RuntimeError("KIMI_API_KEY not configured. Set KIMI_API_KEY in environment.")

    current_date_utc = datetime.now(timezone.utc).date().isoformat()

    if current_state:
        # Update mode
        prompt = (
            "You are an order processing assistant. Below is the CURRENT state of an order in JSON format:\n"
            f"{json.dumps(current_state, indent=2)}\n\n"
            f"The customer has sent a NEW message: \"{order_text}\"\n\n"
            "Your task is to UPDATE the order state based on this new message. Follow these rules:\n"
            "1. KEEP all existing information that is not contradicted or changed by the new message.\n"
            "2. If the message adds new items, append them to the 'items' list.\n"
            "3. If the message provides missing details (like delivery address or country), fill them in.\n"
            "4. If the message corrects existing info, update those fields.\n"
            "5. Maintain the same JSON structure as the input.\n"
            "6. Return ONLY the complete updated JSON object.\n"
            f"Reference date for relative date interpretation: {current_date_utc}.\n"
        )
    else:
        # Extraction mode
        prompt = (
            "Extract ALL order information from the following text and return ONLY valid JSON.\n"
            "The text may be in French or English. Preserve raw wording when possible and infer structured values when obvious.\n"
            f"Reference date for relative date interpretation: {current_date_utc}.\n"
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
            "- For date, return ISO-8601 when you can infer it; use the reference date to resolve relative dates like 'next Friday'. Otherwise null.\n"
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


def build_order_lines(parsed_items: list, products: dict) -> List[OrderLine]:
    """Build OrderLine objects from parsed items and inventory products"""
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
    return order_lines


def find_missing_fields_and_generate_prompt(
    json_data: dict,
    required_fields: Optional[List[str]] = None,
    request_id: Optional[str] = None
) -> dict[str, Any]:
    """
    Find missing fields in parsed JSON and use Kimi to generate a prompt text asking for them.
    Optionally saves results to database.
    
    Args:
        json_data: The JSON object to check for missing fields
        required_fields: List of required field names. If None, uses defaults for order parsing
        request_id: Optional request ID to save results to database
    
    Returns:
        {
            "missing_fields": List[str],
            "prompt_text": str,
            "has_missing_fields": bool
        }
    """
    if client is None:
        raise RuntimeError("KIMI_API_KEY not configured. Set KIMI_API_KEY in environment.")
    
    # Default required fields for order parsing
    if required_fields is None:
        required_fields = [
            "delivery.raw_address",
            "delivery.country",
            "items",
        ]
    
    # Find missing fields
    missing_fields = []
    
    for field_path in required_fields:
        parts = field_path.split(".")
        value = json_data
        
        # Navigate through nested structure
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        
        # Check if field is missing, None, or empty
        if value is None or value == "" or (isinstance(value, list) and len(value) == 0):
            missing_fields.append(field_path)
    
    # If no missing fields, return early
    if not missing_fields:
        return {
            "missing_fields": [],
            "prompt_text": "",
            "has_missing_fields": False
        }
    
    # Generate prompt text using Kimi
    field_descriptions = {
        "delivery.raw_address": "delivery address",
        "delivery.country": "delivery country",
        "items": "product items/lines for the order",
    }
    
    missing_descriptions = [field_descriptions.get(f, f) for f in missing_fields]
    
    prompt = f"""Generate a concise and direct message asking the customer for the following missing information:
{', '.join(missing_descriptions)}

Make the message professional but friendly. Keep it to one short paragraph. 
Use the language appropriate for business context (French or English).
Focus on what is needed, be direct and to the point.

Return ONLY the message text, no introduction or explanation."""
    
    response = client.chat.completions.create(
        model="kimi-k2.6",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )
    
    prompt_text = response.choices[0].message.content.strip()
    
    logger.info(f"Generated Kimi message: {prompt_text}")
    
    # Save to database if request_id provided
    if request_id:
        update_request_field(request_id, "missing_fields_json", missing_fields)
        update_request_field(request_id, "prompt_text", prompt_text)
    
    return {
        "missing_fields": missing_fields,
        "prompt_text": prompt_text,
        "has_missing_fields": len(missing_fields) > 0
    }

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

        order_lines = build_order_lines(parsed_items, products)

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

        # Save parsed order to database
        save_request_to_db(
            request_id,
            parsed,
            order_text=request.order_text,
        )
        
        # Check for missing required fields and generate prompt
        missing_fields_result = find_missing_fields_and_generate_prompt(
            parsed,
            request_id=request_id
        )

        response = OrderResponse(
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
        
        # Add missing fields info to response
        response_dict = response.model_dump()
        if missing_fields_result["has_missing_fields"]:
            response_dict["missing_fields"] = missing_fields_result["missing_fields"]
            response_dict["prompt_text"] = missing_fields_result["prompt_text"]
        
        return JSONResponse(status_code=200, content=response_dict)
    
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
            "GET /request/{request_id}": "Retrieve a parsed request",
            "GET /requests/missing": "Find all requests with missing fields",
            "POST /request/{request_id}/update": "Update a request with new order text",
            "POST /request/{request_id}/field": "Update a specific field in a request",
            "GET /quote/{order_id}": "Generate a quote PDF for an order",
            "GET /health": "Health check",
            "GET /app": "Interactive SMS chat app",
            "GET /docs": "API documentation"
        }
    }


@app.get("/app")
async def get_app():
    """Serve the SMS chat web app"""
    app_path = BASE_DIR / "app.html"
    if not app_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    return FileResponse(app_path, media_type="text/html")


# ==================== Database Endpoints ====================

@app.get("/request/{request_id}")
async def get_request(request_id: str):
    """Retrieve a parsed request from the database"""
    request_data = get_request_from_db(request_id)
    
    if not request_data:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    
    # Parse JSON fields
    if request_data.get("parsed_json"):
        request_data["parsed_json"] = json.loads(request_data["parsed_json"])
    if request_data.get("items_json"):
        request_data["items_json"] = json.loads(request_data["items_json"])
    if request_data.get("missing_fields_json"):
        request_data["missing_fields_json"] = json.loads(request_data["missing_fields_json"])
    
    return request_data


@app.get("/requests/missing")
async def get_requests_with_missing_fields(limit: int = Query(10, ge=1, le=100)):
    """Find all requests with missing required fields"""
    requests = find_requests_with_missing_fields(limit)
    
    # Parse JSON fields
    for req in requests:
        if req.get("parsed_json"):
            req["parsed_json"] = json.loads(req["parsed_json"])
        if req.get("items_json"):
            req["items_json"] = json.loads(req["items_json"])
        if req.get("missing_fields_json"):
            req["missing_fields_json"] = json.loads(req["missing_fields_json"])
    
    return {"total": len(requests), "requests": requests}


@app.post("/request/{request_id}/update")
async def update_request_with_text(request_id: str, update: UpdateRequest):
    """Update a request with new order text and re-parse"""
    new_order_text = update.new_order_text
    existing = get_request_from_db(request_id)
    
    if not existing:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    
    try:
        # Re-parse with new text, merging with existing state
        products = load_products()
        previous_parsed = json.loads(existing['parsed_json']) if existing.get('parsed_json') else None
        parsed = parse_order_with_kimi(new_order_text, current_state=previous_parsed)
        
        # Re-build order lines with product matching
        parsed_items = parsed.get('items', []) if isinstance(parsed, dict) else []
        order_lines = build_order_lines(parsed_items, products)
        
        # Update combined order text
        combined_text = f"{existing['order_text']}\n---\n{new_order_text}"
        
        # Save updated information
        save_request_to_db(request_id, parsed, order_text=combined_text)
        
        # Check for missing fields again
        missing_fields_result = find_missing_fields_and_generate_prompt(
            parsed,
            request_id=request_id
        )
        
        updated = get_request_from_db(request_id)
        
        # Parse JSON fields
        if updated.get("parsed_json"):
            updated["parsed_json"] = json.loads(updated["parsed_json"])
        if updated.get("items_json"):
            updated["items_json"] = json.loads(updated["items_json"])
        if updated.get("missing_fields_json"):
            updated["missing_fields_json"] = json.loads(updated["missing_fields_json"])
        
        return {
            "meta": {"request_id": request_id},
            "order_lines": order_lines,
            "missing_fields": missing_fields_result["missing_fields"],
            "prompt_text": missing_fields_result["prompt_text"]
        }
        
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating request: {str(e)}")


@app.post("/request/{request_id}/field")
async def update_request_field_endpoint(request_id: str, field_name: str, value: str):
    """Update a specific field in a request"""
    existing = get_request_from_db(request_id)
    
    if not existing:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")
    
    try:
        update_request_field(request_id, field_name, value)
        
        updated = get_request_from_db(request_id)
        
        # Parse JSON fields
        if updated.get("parsed_json"):
            updated["parsed_json"] = json.loads(updated["parsed_json"])
        if updated.get("items_json"):
            updated["items_json"] = json.loads(updated["items_json"])
        if updated.get("missing_fields_json"):
            updated["missing_fields_json"] = json.loads(updated["missing_fields_json"])
        
        return {"success": True, "request": updated}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating field: {str(e)}")



def build_pdf_response(order_id: str) -> Response:
    """Build a PDF response for a quote"""
    config = load_config()
    supplier = config["supplier"]
    data_path = resolve_path(config["data"].get("path", config["data"]["excel_path"]))
    export_path = resolve_path(config["quote_export"]["path"])
    template_path = resolve_path(config["quote"]["template_path"])
    logo_path = resolve_path(supplier.get("logo_path", "assets/logo.png"))

    try:
        data = load_data(data_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Fichier de données introuvable: {data_path}",
        ) from exc

    try:
        order = get_order(data, order_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Order not found: {order_id}",
        ) from exc

    totals = calculate_totals(order, config)

    try:
        pdf_bytes, quote_number = generate_quote_pdf(
            order_id=order_id,
            excel_path=data_path,
            config=config,
            export_path=export_path,
            template_path=template_path,
            logo_path=logo_path,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Generation PDF impossible: {exc}",
        ) from exc

    filename = f"quote_{quote_number:05d}_{order_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Quote-Number": str(quote_number),
            "X-Order-Id": order_id,
            "X-Total-TTC": str(totals["total_ttc"]),
        },
    )


@app.get("/quote/{order_id}")
async def generate_quote_from_path(order_id: str) -> Response:
    """Generate a quote PDF from an order ID"""
    return build_pdf_response(order_id.strip())


@app.get("/generate-quote")
async def generate_quote_from_query(order_id: str = Query(...)) -> Response:
    """Generate a quote PDF from a query parameter"""
    return build_pdf_response(order_id.strip())


def build_pdf_response_from_db(request_id: str) -> Response:
    """Build a PDF response for a quote using data from SQLite database"""
    config = load_config()
    supplier = config["supplier"]
    export_path = resolve_path(config["quote_export"]["path"])
    template_path = resolve_path(config["quote"]["template_path"])
    logo_path = resolve_path(supplier.get("logo_path", "assets/logo.png"))

    # 1. Get request from DB
    req_data = get_request_from_db(request_id)
    if not req_data:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")

    # 2. Load products for prices and SKUs
    products_db = load_products()
    
    # 3. Build lines from items_json
    items_raw = json.loads(req_data.get('parsed_json', '{}')).get('items', [])
    order_lines_pydantic = build_order_lines(items_raw, products_db)
    
    lines = []
    for line in order_lines_pydantic:
        qty = line.quantity or 1
        unit_price = Decimal(str(line.pricing.stock_unit_price or 0))
        line_total = qty * unit_price
        
        lines.append({
            "line_id": line.line_id,
            "sku": line.product.normalized.sku or "N/A",
            "product_name": line.product.raw_description,
            "description_specs": "",
            "qty": qty,
            "unit": "unit",
            "unit_price_eur": unit_price,
            "line_total_ht": line_total,
            "unit_price_eur_fmt": _format_money(unit_price),
            "line_total_ht_fmt": _format_money(line_total),
        })

    # 4. Build order object
    order = {
        "order_id": request_id,
        "order_date": req_data['created_at'][:10],
        "delivery_address": req_data.get('delivery_address'),
        "delivery_date": None,
        "has_express": False
    }

    # 5. Build client object
    client = {
        "company_name": "Client",
        "contact_name": "",
        "address": req_data.get('delivery_address') or "",
        "city": req_data.get('delivery_country') or "",
        "siret": "",
        "email": "",
        "phone": ""
    }

    # 6. Calculate totals
    totals = calculate_totals({"lines": lines}, config)

    # 7. Generate PDF
    emission = date.today()
    validity = emission + timedelta(days=int(config["quote"]["validity_days"]))
    logo_b64 = load_logo_base64(logo_path)
    
    # Prepare Jinja environment
    template_file = Path(template_path)
    environment = Environment(
        loader=FileSystemLoader(str(template_file.parent)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template(template_file.name)
    
    # We need a dummy quote number for DB requests
    # Use part of the request_id or a separate sequence
    quote_number = int(datetime.now().strftime("%H%M%S"))
    quote_year = datetime.now().year

    html = template.render(
        supplier=supplier,
        client=client,
        order=order,
        lines=lines,
        totals=totals,
        quote_number=quote_number,
        quote_year=quote_year,
        emission_date=emission.strftime("%d/%m/%Y"),
        validity_date=validity.strftime("%d/%m/%Y"),
        logo_b64=logo_b64,
        config=config,
        has_express=False,
        express_fee=Decimal("0"),
    )
    
    pdf_bytes = render_pdf(html)
    filename = f"quote_{request_id}.pdf"
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Quote-Number": str(quote_number),
            "X-Order-Id": request_id,
            "X-Total-TTC": str(totals["total_ttc"]),
        },
    )


@app.get("/request/{request_id}/quote")
async def generate_quote_from_request_id(request_id: str) -> Response:
    """Generate a quote PDF from a SQLite request ID"""
    return build_pdf_response_from_db(request_id.strip())

# ==================== Dashboard API Endpoints ====================

@app.get("/api/dashboard/clients")
async def dashboard_clients():
    """Retrieve clients list for the dashboard"""
    clients_path = DB_PATH.parent / "database" / "clients.csv"
    if not clients_path.exists():
        return {"clients": []}
        
    clients = []
    with open(clients_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            clients.append({
                "client_id": row.get("client_id", ""),
                "company_name": row.get("company_name", ""),
                "client_score": float(row.get("client_score", "0.5").replace(",", ".")) if row.get("client_score") else 0.5,
            })
    return {"clients": clients}


@app.get("/api/dashboard/products")
async def dashboard_products():
    """Retrieve products list for the dashboard"""
    products_path = DB_PATH.parent / "database" / "products.csv"
    if not products_path.exists():
        return {"products": []}

    with products_path.open("r", encoding="utf-8") as handle:
        first_line = handle.readline()
        delimiter = ";" if ";" in first_line and first_line.count(";") > first_line.count(",") else ","
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delimiter)
        products = []
        for row in reader:
            products.append({
                "sku_code": row.get("sku_code", ""),
                "product_name": row.get("product_name", ""),
                "brand": row.get("brand", ""),
                "category": row.get("category", ""),
                "unit": row.get("unit", ""),
                "price_per_unit": row.get("price_per_unit", ""),
                "quantity_available": row.get("quantity_available", ""),
                "moq": row.get("MOQ", row.get("moq", "")),
                "refill_time": row.get("refill_time", row.get("refill time", "")),
                "description": row.get("description", ""),
                "status": row.get("status", ""),
            })

    return {"products": products}


@app.get("/api/dashboard/orders")
async def dashboard_orders():
    """Retrieve all orders for the dashboard with their computed statuses"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customer_requests ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()

    orders_out = []
    for row in rows:
        req = dict(row)
        stored_status = req.get("status", "pending")
        dashboard_status = stored_status
        proposals = None
        review_reason = None

        missing = json.loads(req.get("missing_fields_json") or "[]")
        if missing:
            dashboard_status = "still_getting_info"
        elif stored_status not in {"quote_sent", "canceled", "error", "still_getting_info"}:
            try:
                parsed = json.loads(req.get("parsed_json") or "{}")
                items = parsed.get("items", [])
                products = load_products()

                # Fast match to avoid slow AI calls on dashboard load
                def fast_match(name, prods):
                    if not name:
                        return "NOT_FOUND"
                    if name in prods:
                        return name
                    l = name.lower()
                    for p in prods:
                        if l in p.lower() or p.lower() in l:
                            return p
                    return "NOT_FOUND"

                lines = []
                default_proposals = []
                for i, item in enumerate(items, 1):
                    pname = (item.get("product") or "").strip()
                    m = fast_match(pname, products)
                    available_qty = products[m].get("quantity_available") if m != "NOT_FOUND" else None
                    sku_out = products[m].get("sku_code") if m != "NOT_FOUND" else None
                    stock_price = products[m].get("price_per_unit") if m != "NOT_FOUND" else None

                    qty_val = item.get("quantity")
                    try:
                        qty_int = int(qty_val) if qty_val is not None else None
                    except Exception:
                        qty_int = None

                    price_val = item.get("wanted_price")
                    try:
                        w_price = float(price_val) if price_val is not None else None
                    except Exception:
                        w_price = None

                    lines.append(
                        OrderLine(
                            line_id=i,
                            product=ProductInfo(
                                raw_description=pname or "MISSING",
                                normalized=ProductNormalized(sku=sku_out),
                                status="FOUND" if m != "NOT_FOUND" else "NOT_FOUND",
                            ),
                            quantity=qty_int,
                            pricing=PricingInfo(
                                wanted_unit_price=w_price,
                                stock_unit_price=stock_price,
                            ),
                        )
                    )

                    default_proposals.append(
                        {
                            "line_id": i,
                            "product": pname or "MISSING",
                            "requested_quantity": qty_int,
                            "available_quantity": available_qty,
                            "wanted_unit_price": w_price,
                            "stock_unit_price": stock_price,
                            "proposal": {
                                "offer_quantity": qty_int,
                                "offer_unit_price": w_price if w_price is not None else stock_price,
                            },
                        }
                    )

                if not lines or all((l.product.status or "").upper() == "NOT_FOUND" for l in lines):
                    dashboard_status = "canceled"
                    review_reason = "All products not found in inventory"
                else:
                    if stored_status == "negotiating":
                        proposals = default_proposals

                    order_response = OrderResponse(
                        meta=MetaInfo(
                            request_id=req["request_id"],
                            created_at="",
                            intake_channel="",
                            language="",
                        ),
                        client=ClientInfo(raw_identity=RawIdentity(), client_score=0.5),
                        order_status="pending_payement",
                        order_lines=lines,
                    )

                    res = await validate_order(order_response)

                    if res.get("result") == "NEGOTIATING":
                        dashboard_status = "negotiating"
                        proposals = res.get("next_step", {}).get("proposals", [])
                    elif res.get("result") == "CANCELED":
                        dashboard_status = "canceled"
                        review_reason = res.get("reason", "Order validation failed")
                    elif res.get("result") == "VALIDATED":
                        # Keep persisted status (for example quote_sent) instead of forcing pending.
                        dashboard_status = stored_status
            except Exception as e:
                logger.error(f"Error validating order {req['request_id']}: {e}")
                dashboard_status = "error"
                review_reason = f"Validation error: {str(e)[:100]}"

        orders_out.append({
            "request_id": req["request_id"],
            "created_at": req["created_at"],
            "order_text": req["order_text"],
            "status": dashboard_status,
            "proposals": proposals,
            "delivery_address": req.get("delivery_address"),
            "delivery_country": req.get("delivery_country"),
            "review_reason": review_reason
        })
        
    return {"orders": orders_out}


@app.post("/api/dashboard/orders/{request_id}/send-quote")
async def dashboard_send_quote(request_id: str):
    """Generate the quote PDF and mark the order as quote_sent"""
    try:
        response = build_pdf_response_from_db(request_id)
        pdf_bytes = response.body
        
        config = load_config()
        template_path = resolve_path(config["quote"]["template_path"])
        output_dir = Path(template_path).resolve().parents[1] / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / f"quote_{request_id}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        
        update_request_field(request_id, "status", "quote_sent")
        return {"success": True, "status": "quote_sent", "pdf_path": str(pdf_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ProposalUpdate(BaseModel):
    proposals: List[dict]


@app.post("/api/dashboard/orders/{request_id}/update-lines")
async def update_order_lines(request_id: str, update: ProposalUpdate):
    """Update order items based on human modifications to the proposals"""
    req = get_request_from_db(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Not found")
    
    parsed = json.loads(req.get("parsed_json") or "{}")
    items = parsed.get("items", [])
    
    for prop in update.proposals:
        line_id = prop.get("line_id")
        if 1 <= line_id <= len(items):
            item = items[line_id - 1]
            item["quantity"] = prop["proposal"]["offer_quantity"]
            item["wanted_price"] = prop["proposal"]["offer_unit_price"]
            
    update_request_field(request_id, "parsed_json", parsed)
    update_request_field(request_id, "items_json", items)
    
    return {"success": True}


@app.get("/api/dashboard/clients/{client_id}/orders")
async def get_client_orders(client_id: str):
    """Fetch all orders for a specific client from orders.csv"""
    try:
        orders_csv_path = DB_PATH.parent / "database" / "orders.csv"
        if not orders_csv_path.exists():
            return {"orders": []}
        
        orders = []
        with open(orders_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if row.get('client_id') == client_id:
                    orders.append({
                        'order_id': row.get('order_id', ''),
                        'request_id': row.get('request_id', ''),
                        'client_id': row.get('client_id', ''),
                        'company_name': row.get('company_name', ''),
                        'order_date': row.get('order_date', ''),
                        'sku_code': row.get('sku_code', ''),
                        'product_name': row.get('product_name', ''),
                        'quantity': row.get('quantity', ''),
                        'unit_price': row.get('unit_price', ''),
                        'total_price': row.get('total_price', ''),
                        'status': row.get('status', ''),
                        'delivery_date': row.get('delivery_date', ''),
                        'invoice_id': row.get('invoice_id', ''),
                    })
        
        return {"orders": orders}
    except Exception as e:
        logger.error(f"Error fetching client orders: {e}")
        return {"orders": [], "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
