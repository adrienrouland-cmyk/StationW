from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "customer_requests.db"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_parsed(
    client_phone: str,
    client_email: str,
    address: str,
    country: str,
    payment_raw: str,
    payment_type: str,
    items: list[dict],
) -> dict:
    return {
        "client": {
            "raw_identity": {"phone": client_phone, "email": client_email},
            "client_score": 0.8,
        },
        "delivery": {
            "raw_address": address,
            "country": country,
            "pick_up": False,
        },
        "payment_terms": {
            "raw": payment_raw,
            "type": payment_type,
            "details": "Standard terms agreed",
        },
        "items": items,
    }


def reseed() -> None:
    backup_path = DB_PATH.with_name(
        f"customer_requests.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )
    shutil.copy2(DB_PATH, backup_path)

    rows = [
        {
            "request_id": "req_seed_20260506_002",
            "company_name": "Eiffage TP Bordeaux",
            "phone": "+33 5 56 78 90 12",
            "email": "p.durand@eiffage.fr",
            "address": "45 Av. du Médoc, 33000 Bordeaux",
            "country": "France",
            "payment_raw": "Bank transfer",
            "payment_type": "bank_transfer",
            "items": [
                {
                    "product": "High-strength hex bolt M12×60 cl.10.9",
                    "quantity": 500,
                    "date": "2026-05-18",
                    "wanted_price": 0.42,
                },
                {
                    "product": "5/2 bistable pneumatic directional valve G1/4",
                    "quantity": 2,
                    "date": "2026-05-18",
                    "wanted_price": 148.0,
                },
            ],
            "order_text": "Need 500 M12x60 bolts and 2 G1/4 bistable valves for delivery on 2026-05-18.",
            "notes": "Seeded request for inventory validation",
            "status": "quote_sent",
            "missing_fields": [],
            "prompt_text": "",
        },
        {
            "request_id": "req_seed_20260506_004",
            "company_name": "Maintenance Pro SARL",
            "phone": "+33 7 88 44 55 66",
            "email": "n.benali@maint-pro.fr",
            "address": "22 ZI des Gatines, 86000 Poitiers",
            "country": "France",
            "payment_raw": "Card",
            "payment_type": "card",
            "items": [
                {
                    "product": "Automatic lubricator SKF SYSTEM 24 125 cm³",
                    "quantity": 2,
                    "date": "2026-05-20",
                    "wanted_price": 42.0,
                },
                {
                    "product": "Voyant lumineux LED 24V Ø 22 mm bleu",
                    "quantity": 10,
                    "date": "2026-05-20",
                    "wanted_price": 12.4,
                },
            ],
            "order_text": "We need 2 SKF SYSTEM 24 lubricators and 10 blue LED indicators for 2026-05-20.",
            "notes": "Seeded request for inventory validation",
            "status": "quote_sent",
            "missing_fields": [],
            "prompt_text": "",
        },
        {
            "request_id": "req_seed_20260506_005",
            "company_name": "Industrie Rhône-Alpes",
            "phone": "+33 4 50 33 44 55",
            "email": "m.fontaine@ira-group.fr",
            "address": "Route de Grenoble, 74000 Annecy",
            "country": "France",
            "payment_raw": "Net 15",
            "payment_type": "net_30",
            "items": [
                {
                    "product": "3/2 directional solenoid valve SMC VF3130",
                    "quantity": 40,
                    "date": "2026-05-17",
                    "wanted_price": 89.5,
                },
                {
                    "product": "Lovejoy L-075 jaw coupling + spider",
                    "quantity": 6,
                    "date": "2026-05-17",
                    "wanted_price": 28.9,
                },
            ],
            "order_text": "Order 40 SMC VF3130 valves and 6 Lovejoy L-075 couplings for delivery on 2026-05-17.",
            "notes": "Negotiating case: requested quantity exceeds available stock",
            "status": "negotiating",
            "missing_fields": [],
            "prompt_text": "Requested quantity exceeds available stock; propose a reduced quantity.",
        },
        {
            "request_id": "req_seed_20260506_009",
            "company_name": "Maintenance Pro SARL",
            "phone": "+33 7 88 44 55 66",
            "email": "n.benali@maint-pro.fr",
            "address": "22 ZI des Gatines, 86000 Poitiers",
            "country": "France",
            "payment_raw": "Card",
            "payment_type": "card",
            "items": [
                {
                    "product": "High-pressure hydraulic filter HF7553",
                    "quantity": 4,
                    "date": "2026-05-22",
                    "wanted_price": 35.0,
                },
                {
                    "product": "Automatic lubricator SKF SYSTEM 24 125 cm³",
                    "quantity": 1,
                    "date": "2026-05-22",
                    "wanted_price": 42.0,
                },
            ],
            "order_text": "Please prepare 4 HF7553 filters and 1 SKF SYSTEM 24 lubricator for 2026-05-22.",
            "notes": "Seeded request for inventory validation",
            "status": "quote_sent",
            "missing_fields": [],
            "prompt_text": "",
        },
        {
            "request_id": "req_seed_20260506_010",
            "company_name": "Industrie Rhône-Alpes",
            "phone": "+33 4 50 33 44 55",
            "email": "m.fontaine@ira-group.fr",
            "address": "Route de Grenoble, 74000 Annecy",
            "country": "France",
            "payment_raw": "Net 15",
            "payment_type": "net_30",
            "items": [
                {
                    "product": "Voyant lumineux LED 24V Ø 22 mm bleu",
                    "quantity": 15,
                    "date": "2026-05-23",
                    "wanted_price": 12.4,
                },
            ],
            "order_text": "Order 15 blue LED 24V indicators for delivery on 2026-05-23.",
            "notes": "Seeded request for inventory validation",
            "status": "quote_sent",
            "missing_fields": [],
            "prompt_text": "",
        },
        {
            "request_id": "req_seed_20260506_013",
            "company_name": "Industrie Rhône-Alpes",
            "phone": "+33 4 50 33 44 55",
            "email": "m.fontaine@ira-group.fr",
            "address": "Route de Grenoble, 74000 Annecy",
            "country": "France",
            "payment_raw": "Net 15",
            "payment_type": "net_30",
            "items": [
                {"product": "Process pressure sensor 0-40 bar G1/4", "quantity": 3, "date": "2026-05-25", "wanted_price": 68.0},
            ],
            "order_text": "Looking for 3 pressure sensors 0-40 bar G1/4 with a better unit price for 2026-05-25.",
            "notes": "Negotiating case: requested unit price is below stock price",
            "status": "negotiating",
            "missing_fields": [],
            "prompt_text": "Requested unit price is below stock price; propose a higher unit price.",
        },
        {
            "request_id": "req_seed_20260506_014",
            "company_name": "Maintenance Pro SARL",
            "phone": "+33 7 88 44 55 66",
            "email": "n.benali@maint-pro.fr",
            "address": "22 ZI des Gatines, 86000 Poitiers",
            "country": "France",
            "payment_raw": "Card",
            "payment_type": "card",
            "items": [
                {"product": "Voyant lumineux LED 24V Ø 22 mm bleu", "quantity": 30, "date": "2026-05-26", "wanted_price": 11.9},
                {"product": "Automatic lubricator SKF SYSTEM 24 125 cm³", "quantity": 2, "date": "2026-05-26", "wanted_price": 42.0},
            ],
            "order_text": "Need 30 blue LED indicators and 2 SKF SYSTEM 24 lubricators for 2026-05-26.",
            "notes": "Seeded request for inventory validation",
            "status": "quote_sent",
            "missing_fields": [],
            "prompt_text": "",
        },
        {
            "request_id": "req_seed_20260506_015",
            "company_name": "Fonderie du Nord",
            "phone": "+33 6 12 34 56 78",
            "email": "t.mercier@fonderie-nord.fr",
            "address": "12 Rue de l'Industrie, 59000 Lille",
            "country": "France",
            "payment_raw": "",
            "payment_type": "unknown",
            "items": [
                {
                    "product": "Deep groove ball bearing 6204-2RS",
                    "quantity": 12,
                    "date": "2026-05-27",
                    "wanted_price": 6.80,
                },
            ],
            "order_text": "Please supply 12 deep groove ball bearings 6204-2RS for delivery on 2026-05-27.",
            "notes": "Seeded request waiting for payment terms confirmation",
            "status": "still_getting_info",
            "missing_fields": ["payment_terms.type"],
            "prompt_text": "We need you to confirm your preferred payment terms before we can finalize the quote.",
        },
        {
            "request_id": "req_seed_20260506_016",
            "company_name": "Ateliers Martin SAS",
            "phone": "+33 4 72 11 22 33",
            "email": "s.martin@ateliers-martin.fr",
            "address": "",
            "country": "France",
            "payment_raw": "Bank transfer",
            "payment_type": "bank_transfer",
            "items": [
                {
                    "product": "Process pressure sensor 0-40 bar G1/4",
                    "quantity": 5,
                    "date": "2026-05-28",
                    "wanted_price": 70.0,
                },
            ],
            "order_text": "Looking for 5 pressure sensors 0-40 bar G1/4 for delivery on 2026-05-28.",
            "notes": "Seeded request waiting for delivery address",
            "status": "still_getting_info",
            "missing_fields": ["delivery.raw_address"],
            "prompt_text": "Could you please confirm the complete delivery address so we can verify shipping logistics?",
        },
    ]

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM customer_requests")
        cur.execute("DELETE FROM sqlite_sequence WHERE name='customer_requests'")

        for row in rows:
            parsed_json = build_parsed(
                row["phone"],
                row["email"],
                row["address"],
                row["country"],
                row["payment_raw"],
                row["payment_type"],
                row["items"],
            )
            cur.execute(
                """
                INSERT INTO customer_requests (
                    request_id, created_at, updated_at, order_text, parsed_json,
                    delivery_address, delivery_country, items_json, missing_fields_json,
                    prompt_text, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["request_id"],
                    now_utc(),
                    now_utc(),
                    row["order_text"],
                    json.dumps(parsed_json, ensure_ascii=False),
                    row["address"],
                    row["country"],
                    json.dumps(row["items"], ensure_ascii=False),
                    json.dumps(row["missing_fields"], ensure_ascii=False),
                    row["prompt_text"],
                    row["status"],
                    row["notes"],
                ),
            )

        conn.commit()
    finally:
        conn.close()

    print(f"backup={backup_path}")
    print(f"inserted={len(rows)}")


if __name__ == "__main__":
    reseed()