from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


class Settings:
    app_name: str = os.getenv("APP_NAME", "mediation")  # 所有外部依赖都从环境变量读取。
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    llm_provider: str = os.getenv("LLM_PROVIDER", "deepseek")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", os.getenv("OPENAI_MODEL", "deepseek-v4-pro"))

    asr_provider: str = os.getenv("ASR_PROVIDER", "tencent")
    realtime_asr_simulation: bool = os.getenv("REALTIME_ASR_SIMULATION", "0") not in {"0", "false", "False"}
    realtime_asr_simulation_interval_ms: int = int(os.getenv("REALTIME_ASR_SIMULATION_INTERVAL_MS", "900"))
    tencent_asr_appid: str = os.getenv("TENCENT_ASR_APPID", "1300915009")
    tencent_asr_secret_id: str = os.getenv("TENCENT_ASR_SECRET_ID", "")
    tencent_asr_secret_key: str = os.getenv("TENCENT_ASR_SECRET_KEY", "")
    tencent_asr_speaker_diarization: bool = os.getenv("TENCENT_ASR_SPEAKER_DIARIZATION", "0") not in {"0", "false", "False"}
    tencent_asr_engine_model_type: str = os.getenv(
        "TENCENT_ASR_ENGINE_MODEL_TYPE",
        "16k_zh_en_speaker_2.0" if tencent_asr_speaker_diarization else "16k_zh",
    )

    frontend_dir: Path = BASE_DIR.parent / "frontend"


@lru_cache
def get_settings() -> Settings:
    return Settings()
