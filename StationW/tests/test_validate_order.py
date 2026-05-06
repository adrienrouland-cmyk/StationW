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


# ==================== Tests for find_missing_fields_and_generate_prompt ====================
import pytest
from unittest.mock import patch, MagicMock
from validator.main import find_missing_fields_and_generate_prompt


class TestFindMissingFields:
    """Test suite for find_missing_fields_and_generate_prompt function"""
    
    def test_no_missing_fields(self):
        """Test when all required fields are present"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": "+33612345678",
                    "email": "customer@example.com"
                }
            },
            "delivery": {
                "raw_address": "123 Main St",
                "country": "France"
            },
            "payment_terms": {
                "raw": "Net 30"
            },
            "items": [
                {"product": "Widget", "quantity": 5}
            ]
        }
        
        with patch('validator.main.client') as mock_client:
            # Should not call Kimi if no missing fields
            result = find_missing_fields_and_generate_prompt(json_data)
            
            assert result["has_missing_fields"] is False
            assert result["missing_fields"] == []
            assert result["prompt_text"] == ""
    
    def test_missing_delivery_address(self):
        """Test when delivery address is missing"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": None,
                    "email": None
                }
            },
            "delivery": {
                "raw_address": None,
                "country": "France"
            },
            "payment_terms": {
                "raw": None
            },
            "items": [{"product": "Widget", "quantity": 5}]
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide your delivery address."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            assert result["has_missing_fields"] is True
            assert "delivery.raw_address" in result["missing_fields"]
            assert result["prompt_text"] == "Please provide your delivery address."
            mock_client.chat.completions.create.assert_called_once()
    
    def test_missing_multiple_fields(self):
        """Test when multiple required fields are missing"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": None,
                    "email": None
                }
            },
            "delivery": {
                "raw_address": None,
                "country": None
            },
            "payment_terms": {
                "raw": None
            },
            "items": []
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "We need your delivery address, country, and product items."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            assert result["has_missing_fields"] is True
            assert len(result["missing_fields"]) == 3  # address, country, items
            assert "delivery.raw_address" in result["missing_fields"]
            assert "delivery.country" in result["missing_fields"]
            assert "items" in result["missing_fields"]
            # Phone, email, and payment terms should NOT be in missing fields
            assert "client.raw_identity.phone" not in result["missing_fields"]
            assert "client.raw_identity.email" not in result["missing_fields"]
            assert "payment_terms.raw" not in result["missing_fields"]
    
    def test_empty_items_array(self):
        """Test when items array is empty"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": "+33612345678",
                    "email": "customer@example.com"
                }
            },
            "delivery": {
                "raw_address": "123 Main St",
                "country": "France"
            },
            "payment_terms": {
                "raw": "Net 30"
            },
            "items": []
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide the items you want to order."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            assert result["has_missing_fields"] is True
            assert "items" in result["missing_fields"]
    
    def test_custom_required_fields(self):
        """Test with custom required fields list"""
        json_data = {
            "name": "John Doe",
            "email": None,
            "phone": "123456789"
        }
        
        custom_fields = ["name", "email", "phone"]
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide your email address."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data, custom_fields)
            
            assert result["has_missing_fields"] is True
            assert "email" in result["missing_fields"]
            assert "name" not in result["missing_fields"]
            assert "phone" not in result["missing_fields"]
    
    def test_empty_string_field(self):
        """Test that empty strings are treated as missing for required fields"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": "",
                    "email": ""
                }
            },
            "delivery": {
                "raw_address": "",
                "country": "France"
            },
            "payment_terms": {
                "raw": ""
            },
            "items": [{"product": "Widget", "quantity": 5}]
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide your delivery address."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            assert result["has_missing_fields"] is True
            assert "delivery.raw_address" in result["missing_fields"]
            # Phone, email, payment_terms are not required
            assert "client.raw_identity.phone" not in result["missing_fields"]
            assert "payment_terms.raw" not in result["missing_fields"]
    
    def test_nested_field_missing_entire_parent(self):
        """Test when required nested field parent is missing"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": "123456789",
                    "email": "test@example.com"
                }
            },
            "items": [{"product": "Widget", "quantity": 5}]
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide delivery information."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            assert result["has_missing_fields"] is True
            # Should detect missing delivery fields
            assert "delivery.raw_address" in result["missing_fields"]
            assert "delivery.country" in result["missing_fields"]
    
    def test_kimi_api_not_configured(self):
        """Test that proper error is raised when KIMI_API_KEY not configured"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": None,
                    "email": "test@example.com"
                }
            },
            "delivery": {"raw_address": "123 Main St", "country": "France"},
            "payment_terms": {"raw": "Net 30"},
            "items": [{"product": "Widget", "quantity": 5}]
        }
        
        with patch('validator.main.client', None):
            with pytest.raises(RuntimeError, match="KIMI_API_KEY not configured"):
                find_missing_fields_and_generate_prompt(json_data)
    
    def test_prompt_text_generation(self):
        """Test that Kimi is called correctly when generating prompt"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": "123456789",
                    "email": "test@example.com"
                }
            },
            "delivery": {
                "raw_address": None,
                "country": None
            },
            "payment_terms": {
                "raw": "Net 30"
            },
            "items": [{"product": "Widget", "quantity": 5}]
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide your delivery address and country."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            # Verify Kimi was called
            mock_client.chat.completions.create.assert_called_once()
            call_args = mock_client.chat.completions.create.call_args
            
            # Check model
            assert call_args.kwargs["model"] == "kimi-k2.6"
            # Check temperature
            assert call_args.kwargs["temperature"] == 0.5
            # Check message structure
            assert len(call_args.kwargs["messages"]) == 1
            assert call_args.kwargs["messages"][0]["role"] == "user"
            assert "delivery address" in call_args.kwargs["messages"][0]["content"]
            assert "delivery country" in call_args.kwargs["messages"][0]["content"]
            # Phone and email should NOT be in the prompt
            assert "customer phone number" not in call_args.kwargs["messages"][0]["content"]
            assert "customer email address" not in call_args.kwargs["messages"][0]["content"]
    
    def test_return_structure(self):
        """Test that return value has correct structure"""
        json_data = {
            "client": {
                "raw_identity": {
                    "phone": None,
                    "email": "test@example.com"
                }
            },
            "delivery": {
                "raw_address": "123 Main St",
                "country": "France"
            },
            "payment_terms": {
                "raw": "Net 30"
            },
            "items": [{"product": "Widget", "quantity": 5}]
        }
        
        with patch('validator.main.client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "Please provide your phone number."
            mock_client.chat.completions.create.return_value = mock_response
            
            result = find_missing_fields_and_generate_prompt(json_data)
            
            # Check all required keys exist
            assert "missing_fields" in result
            assert "prompt_text" in result
            assert "has_missing_fields" in result
            
            # Check types
            assert isinstance(result["missing_fields"], list)
            assert isinstance(result["prompt_text"], str)
            assert isinstance(result["has_missing_fields"], bool)
