from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from quote_engine import calculate_totals, generate_quote_pdf, get_order, load_data


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"


def resolve_path(path_value: str) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str(BASE_DIR / path)


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_pdf_response(order_id: str) -> Response:
    config = load_config()
    supplier = config["supplier"]
    data_path = resolve_path(config["data"].get("path", config["data"]["excel_path"]))
    db_path = resolve_path(config["db"]["path"])
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
            detail=f"Commande introuvable: {order_id}",
        ) from exc

    totals = calculate_totals(order, config)

    try:
        pdf_bytes, quote_number = generate_quote_pdf(
            order_id=order_id,
            excel_path=data_path,
            config=config,
            db_path=db_path,
            template_path=template_path,
            logo_path=logo_path,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Generation PDF impossible: {exc}",
        ) from exc

    filename = f"devis_{quote_number:05d}_{order_id}.pdf"
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


app = FastAPI(
    title="Quote Generator",
    description="Generates and stores PDF quotes from a local CSV/Excel data source.",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def generate_quote_from_query(order_id: str = Query(...)) -> Response:
    return build_pdf_response(order_id.strip())


@app.get("/quote/{order_id}")
def generate_quote_from_path(order_id: str) -> Response:
    return build_pdf_response(order_id.strip())


@app.exception_handler(HTTPException)
def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
