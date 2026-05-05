"""
Example test script to test the Order Parser API
Run this after starting the server with: python main.py
"""

import requests
import json

BASE_URL = "http://localhost:8000"

def test_parse_order():
    """Test the /parse-order endpoint"""
    
    test_cases = [
        {
            "name": "Multiple products with dates",
            "order": "I need 3 laptops for tomorrow and 5 wireless mice for next week. Also 2 mechanical keyboards for Friday."
        },
        {
            "name": "Product with quantity only",
            "order": "Can I get 10 USB-C cables?"
        },
        {
            "name": "Single product without date",
            "order": "I need a monitor, preferably a 27 inch one"
        },
        {
            "name": "Product with synonym",
            "order": "I'd like to order 1 laptop and some wireless peripherals - maybe a mouse and keyboard"
        },
        {
            "name": "Non-existent product",
            "order": "I want to buy 5 iPhones and 2 laptops"
        }
    ]
    
    for test in test_cases:
        print(f"\n{'='*70}")
        print(f"Test: {test['name']}")
        print(f"{'='*70}")
        print(f"Order Input: {test['order']}\n")
        
        try:
            response = requests.post(
                f"{BASE_URL}/parse-order",
                json={"order_text": test['order']},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                print("✓ Response (200 OK):")
                print(json.dumps(result, indent=2))
            else:
                print(f"✗ Error ({response.status_code}):")
                print(response.text)
        
        except requests.exceptions.ConnectionError:
            print("✗ Could not connect to server. Make sure it's running on http://localhost:8000")
            return
        except Exception as e:
            print(f"✗ Error: {e}")

def test_health():
    """Test the /health endpoint"""
    print(f"\n{'='*70}")
    print("Health Check")
    print(f"{'='*70}\n")
    
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✓ Server is healthy:")
            print(json.dumps(response.json(), indent=2))
        else:
            print(f"✗ Server health check failed: {response.status_code}")
    except Exception as e:
        print(f"✗ Could not reach server: {e}")

if __name__ == "__main__":
    print("\n" + "="*70)
    print("Order Parser API - Test Script")
    print("="*70)
    
    test_health()
    test_parse_order()
    
    print(f"\n{'='*70}")
    print("Tests Complete!")
    print("="*70 + "\n")
