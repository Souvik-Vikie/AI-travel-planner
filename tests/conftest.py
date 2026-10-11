import pytest

from travel_agent.config import settings
from travel_agent import llm


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "none")
    monkeypatch.setattr(settings, "use_open_meteo", False)
    monkeypatch.setattr(settings, "duffel_token", "")
    monkeypatch.setattr(settings, "serpapi_key", "")
    monkeypatch.setattr(settings, "output_dir", str(tmp_path))
    monkeypatch.setattr(settings, "mock_sold_out_rate", 0.0)
    llm.get_chat_model.cache_clear()
    yield
