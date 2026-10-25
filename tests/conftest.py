import pytest

MODEL_ENV = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
             "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "TOUCHLINE_LLM_MOMENTS",
             "TOUCHLINE_MIN_CALL_INTERVAL", "TOUCHLINE_LIVE_LLM")


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    """Tests never touch a real model or voice, whatever is configured on the machine running them."""
    for k in MODEL_ENV:
        monkeypatch.delenv(k, raising=False)
