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


def make_client(client_score=0.5):
    return {"client": {"raw_identity": {"phone": None, "email": None}, "client_score": client_score}}


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
    """Order should be VALIDATED when qty available and wanted price is not too low"""
    order = {
        **_meta,
        **make_client(0.5),
        "order_status": "pending",
        "order_lines": [
            make_line(
                1,
                "Deep groove ball bearing 6204-2RS",
                "SKF-6204-2RS",
                "FOUND",
                10,
                6.8,
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
        **make_client(0.5),
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
    # available_quantity should reflect inventory (44), offered_quantity should also be 44
    assert proposals[0].get("available_quantity") == 44
    assert proposals[0].get("proposal", {}).get("offer_quantity") == 44


def test_negotiating_due_to_price_with_discount():
    """Order should negotiate when client asks too low; score=1 allows up to 20% discount"""
    # SKF-6204-2RS: stock price = 6.80, wanted = 5.00 (too low)
    # score=1.0 => max discount 20% => minimum acceptable = 6.8 * 0.8 = 5.44
    # offered_price = max(5.00, 5.44) = 5.44
    order = {
        **_meta,
        **make_client(1.0),
        "order_status": "pending",
        "order_lines": [
            make_line(
                1,
                "Deep groove ball bearing 6204-2RS",
                "SKF-6204-2RS",
                "FOUND",
                5,
                5.0,
                6.8,
            )
        ],
    }

    r = client.post("/validate-order", json=order)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("result") == "NEGOTIATING"
    proposals = body.get("next_step", {}).get("proposals")
    assert len(proposals) == 1
    offer = proposals[0].get("proposal", {}).get("offer_unit_price")
    assert abs(offer - 5.44) < 0.01


def test_price_discount_zero_score():
    """Client with score 0 should get no discount when asked price is too low"""
    # SKF-6204-2RS: stock price = 6.80, wanted = 5.00 (too low)
    # score=0.0 => max discount 0% => minimum acceptable = 6.80
    # offered_price = max(5.00, 6.80) = 6.80
    order = {
        **_meta,
        **make_client(0.0),
        "order_status": "pending",
        "order_lines": [
            make_line(
                1,
                "Deep groove ball bearing 6204-2RS",
                "SKF-6204-2RS",
                "FOUND",
                5,
                5.0,
                6.8,
            )
        ],
    }

    r = client.post("/validate-order", json=order)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("result") == "NEGOTIATING"
    proposals = body.get("next_step", {}).get("proposals")
    assert len(proposals) == 1
    offer = proposals[0].get("proposal", {}).get("offer_unit_price")
    assert abs(offer - 6.8) < 0.01


def test_canceled_when_all_not_found():
    """Order should be CANCELED when all lines are NOT_FOUND or order_lines empty"""
    order = {
        **_meta,
        **make_client(0.5),
        "order_status": "pending",
        "order_lines": [
            make_line(1, "Unknown item", "", "NOT_FOUND", 1, None, None)
        ],
    }

    r = client.post("/validate-order", json=order)
    assert r.status_code == 200, r.text
    assert r.json().get("result") == "CANCELED"
