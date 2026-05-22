import pytest
import concurrent.futures
from fastapi.testclient import TestClient
from burrow.api.app import app
from burrow.config import settings

client = TestClient(app)

def test_api_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "burrow"}

def test_api_health_detailed():
    response = client.get("/api/v1/health/detailed")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "llm_provider" in data

def test_api_analyze_payload_size_limit():
    # Make request content exceed maximum allowed size
    max_size = settings.max_request_size
    huge_content = "A" * (max_size + 100)
    
    response = client.post(
        "/api/v1/analyze",
        json={"content": huge_content, "project_root": "."}
    )
    
    # Assert payload too large (413)
    assert response.status_code == 413
    assert "Request payload too large" in response.json()["detail"]

def test_api_analyze_malformed_json():
    # Send bad JSON structure
    # FastAPI automatically handles parameter parsing validation
    response = client.post(
        "/api/v1/analyze",
        content="{\"content\": ",  # incomplete JSON
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422  # Unprocessable Entity

def test_api_concurrency():
    trace = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    x = 1 / 0\n"
        "ZeroDivisionError: division by zero\n"
    )
    
    # Verify we can make 15 parallel requests to the API without locks or thread collisions
    def make_request():
        res = client.post(
            "/api/v1/analyze",
            json={"content": trace, "project_root": "."}
        )
        return res.status_code
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_request) for _ in range(15)]
        results = [f.result() for f in futures]
        
    # All requests should return 200 OK
    assert all(status == 200 for status in results)
