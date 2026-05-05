from __future__ import annotations

import base64
import csv
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape


MONEY_QUANT = Decimal("0.01")


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


def load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    with Path(config_path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


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
