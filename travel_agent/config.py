"""Runtime settings, read from environment / .env."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    llm_backend: str = os.getenv("LLM_BACKEND", "auto")
    hf_token: str = os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or os.getenv("HF_TOKEN", "")
    hf_model_id: str = os.getenv("HF_MODEL_ID", "Qwen/Qwen2.5-7B-Instruct")
    hf_local_model_id: str = os.getenv("HF_LOCAL_MODEL_ID", "Qwen/Qwen2.5-0.5B-Instruct")

    duffel_token: str = os.getenv("DUFFEL_API_TOKEN", "")
    serpapi_key: str = os.getenv("SERPAPI_API_KEY", "")
    use_open_meteo: bool = os.getenv("USE_OPEN_METEO", "1") == "1"

    flex_days: int = int(_f("FLEX_DAYS", 2))
    flex_min_saving: float = _f("FLEX_MIN_SAVING", 0.08)
    max_replans: int = int(_f("MAX_REPLANS", 4))
    mock_sold_out_rate: float = _f("MOCK_SOLD_OUT_RATE", 0.15)
    misc_buffer: float = 0.05  # 5% contingency added to every plan

    # Approximate FX rates to INR used to normalise provider prices (edit as needed).
    fx_to_inr: dict = field(default_factory=lambda: {
        "INR": 1.0, "USD": 88.0, "EUR": 102.0, "GBP": 118.0, "AED": 24.0, "SGD": 68.0, "THB": 2.7,
    })

    cache_dir: str = os.getenv("CACHE_DIR", ".cache")
    output_dir: str = os.getenv("OUTPUT_DIR", "output")

    @property
    def flight_provider(self) -> str:
        if self.duffel_token:
            return "duffel"
        if self.serpapi_key:
            return "serpapi"
        return "mock"

    @property
    def hotel_provider(self) -> str:
        return "serpapi" if self.serpapi_key else "mock"


settings = Settings()


def to_inr(amount: float, currency: str) -> float:
    return float(amount) * settings.fx_to_inr.get(currency.upper(), 1.0)
