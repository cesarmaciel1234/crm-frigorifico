from app import create_app
import json

app = create_app()
client = app.test_client()

# Create a client
rc = client.post("/api/clientes", json={"nombre": "Inspect Cliente Detail", "techo_deuda": 500000.0, "scoring": "A"})
print("Create status:", rc.status_code)
client_data = rc.get_json()
print("Create response:", client_data)
cid = client_data.get("id")

if cid:
    # Get client detail
    rd = client.get(f"/api/clientes/{cid}")
    print("Detail status:", rd.status_code)
    print("Detail response keys:", list(rd.get_json().keys()))
    print("Detail response JSON:", rd.get_json())
else:
    print("Failed to get client ID")
