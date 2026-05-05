"""
FastAPI Validator for Order Parsing
Parses natural language order strings using Kimi API and matches products with inventory
Includes quote generation functionality
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse
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
    # Try common filenames and delimiters (some CSVs use semicolon)
    candidate_paths = ["database/stock.csv", "database/products.csv", "database/products.csv"]
    csv_path = None
    for p in candidate_paths:
        if os.path.exists(p):
            csv_path = p
            break

    if not csv_path:
        raise FileNotFoundError(f"CSV file not found in database/ (tried stock.csv and products.csv)")

    # detect delimiter from first line
    with open(csv_path, 'r', encoding='utf-8') as f:
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
def parse_order_with_kimi(order_text: str) -> List[dict]:
    """
    Use Kimi API to parse natural language order text
    Returns list of dicts with product, quantity, and date
    """
    if client is None:
        raise RuntimeError("KIMI_API_KEY not configured. Set KIMI_API_KEY in environment.")

    current_date_utc = datetime.now(timezone.utc).date().isoformat()

    # Ask Kimi to extract all order fields that feed the response envelope.
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
            "GET /quote/{order_id}": "Generate a quote PDF for an order",
            "GET /": "Query parameter version - GET /?order_id=...",
            "GET /health": "Health check",
            "GET /docs": "API documentation"
        }
    }

# ==================== Quote Generation Endpoints ====================

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
