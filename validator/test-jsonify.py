import requests
payload = {
  "order_text": "client-123: Please send 2 laptops by next Friday (willing to pay 950 each). Also urgently need 1 iPhone for a demo.",
  "client_id": "client-123"
}
resp = requests.post("http://0.0.0.0:8000/parse-order", json=payload, timeout=30)
print(resp.status_code)
print(resp.json())