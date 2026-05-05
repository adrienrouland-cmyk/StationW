import os
import sys
from fastapi.testclient import TestClient

# ensure project root is on sys.path so 'validator' package is importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from validator.main import app

client = TestClient(app)

_meta = {"_meta": {"request_id": "t1", "created_at": "2026-05-05T00:00:00Z", "intake_channel": "test", "language": "fr"}}
_client = {"client": {"raw_identity": {"phone": None, "email": None}}}


def make_line(line_id, product_name, sku, status, qty, wanted_price, stock_price):
    return {
        "line_id": line_id,
        "product": {
            "raw_description": product_name,
            "normalized": {"sku": sku},
            "status": status,
        },
        "quantity": qty,
        "date_wanted": None,
        "pricing": {"wanted_unit_price": wanted_price, "stock_unit_price": stock_price},
    }


def test_validated_order():
    """Order should be VALIDATED when qty available and stock price >= wanted price"""
    order = {
        **_meta,
        **_client,
        "order_status": "pending",
        "order_lines": [
            make_line(
                1,
                "Deep groove ball bearing 6204-2RS",
                "SKF-6204-2RS",
                "FOUND",
                10,
                6.0,
                6.8,
            )
        ],
    }

    r = client.post("/validate-order", json=order)
    assert r.status_code == 200, r.text
    assert r.json().get("result") == "VALIDATED"


def test_negotiating_due_to_quantity():
    """Order should return NEGOTIATING when requested quantity exceeds inventory"""
    # HYDAC-HF7553 has quantity_available 44 in database/products.csv
    order = {
        **_meta,
        **_client,
        "order_status": "pending",
        "order_lines": [
            make_line(1, "High-pressure hydraulic filter HF7553", "HYDAC-HF7553", "FOUND", 100, 35.0, 35.0)
        ],
    }

    r = client.post("/validate-order", json=order)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("result") == "NEGOTIATING"
    proposals = body.get("next_step", {}).get("proposals")
    assert isinstance(proposals, list) and len(proposals) >= 1
    # available_quantity should reflect inventory (44)
    assert proposals[0].get("available_quantity") == 44


def test_canceled_when_all_not_found():
    """Order should be CANCELED when all lines are NOT_FOUND or order_lines empty"""
    order = {
        **_meta,
        **_client,
        "order_status": "pending",
        "order_lines": [
            make_line(1, "Unknown item", "", "NOT_FOUND", 1, None, None)
        ],
    }

    r = client.post("/validate-order", json=order)
    assert r.status_code == 200, r.text
    assert r.json().get("result") == "CANCELED"
