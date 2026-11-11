from fastapi.testclient import TestClient
from app.main import app


def test_server_timing_preserves_public_and_unauthorized_responses():
    client = TestClient(app)
    public = client.get('/api/version')
    assert public.status_code == 200
    assert 'version' in public.json()
    denied = client.get('/api/contracts?building=DEMO_A')
    assert denied.status_code == 401
    for response in (public, denied):
        value = response.headers['server-timing']
        assert value.startswith('app;dur=')
        assert float(value.removeprefix('app;dur=')) >= 0
        assert response.headers['x-content-type-options'] == 'nosniff'
