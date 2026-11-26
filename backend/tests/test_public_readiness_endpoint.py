from fastapi.testclient import TestClient

from app_factory import create_app


def test_public_readyz_is_coarse_and_non_sensitive():
    client = TestClient(create_app())

    response = client.get('/readyz?strict=true')

    assert response.status_code == 200
    assert response.json() == {
        'status': 'ready',
        'service': 'Autonomous Trading Bot',
    }
    serialized = response.text.lower()
    assert 'jwt' not in serialized
    assert 'secret' not in serialized
    assert 'mongo' not in serialized
    assert 'coinbase' not in serialized
    assert 'allowed_symbols' not in serialized
    assert 'trading_mode' not in serialized
