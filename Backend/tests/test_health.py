from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_available():
    assert client.get("/docs").status_code == 200



def test_invalid_endpoint():
    response = client.get("/invalid-endpoint")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_openapi_schema():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "openapi" in response.json()
    assert "paths" in response.json()

