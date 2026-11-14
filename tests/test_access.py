from secrets import token_hex

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import require_basic_auth


def test_protection_covers_reads_and_rejects_cross_site_writes():
    app = FastAPI()

    @app.api_route('/private', methods=['GET', 'PUT'])
    def private():
        return {'ok': True}

    username = token_hex(8)
    password = token_hex(16)
    credentials = (username, password)
    require_basic_auth(app, username, password)
    with TestClient(app) as client:
        assert client.get('/private').status_code == 401
        assert client.get('/private', auth=(username, password + '-invalid')).status_code == 401
        assert client.get('/private', auth=credentials).json() == {'ok': True}
        assert client.put('/private', auth=credentials,
                          headers={'Origin': 'https://elsewhere.example'}).status_code == 403
        assert client.put('/private', auth=credentials,
                          headers={'Origin': 'http://testserver'}).status_code == 200
