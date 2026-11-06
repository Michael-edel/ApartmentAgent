from importlib.metadata import version
from pathlib import Path
import tomllib

import pytest


@pytest.mark.asyncio
async def test_app_and_health_match_installed_release():
    from app.main import app, health

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    release = project["project"]["version"]
    assert version("apartment-agent") == release
    assert app.version == release
    assert (await health())["version"] == release
