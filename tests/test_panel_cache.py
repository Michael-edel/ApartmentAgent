import httpx
import pytest


@pytest.mark.asyncio
async def test_panel_and_worker_cannot_be_reused_from_http_cache():
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://panel.test") as client:
        for path, content_type in (("/?release=0.15.1", "text/html"), ("/service-worker.js", "application/javascript")):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["content-type"].startswith(content_type)

