from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BASE_DIR.parent

# 先读仓库根目录，再读 backend/.env，便于兼容两种常见放置位置。
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")

ASR_MODE_REALTIME = "realtime"
ASR_MODE_REALTIME_DIARIZATION = "realtime_diarization"
ASR_MODES = {ASR_MODE_REALTIME, ASR_MODE_REALTIME_DIARIZATION}


def _env_bool(name: str, default: str = "0") -> bool:
    """统一解析布尔环境变量。"""
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def _resolve_asr_mode() -> str:
    """解析 ASR_MODE，只保留真实拾音模式。"""
    configured_mode = os.getenv("ASR_MODE", ASR_MODE_REALTIME).strip().lower() or ASR_MODE_REALTIME
    if configured_mode not in ASR_MODES:
        raise ValueError(
            "ASR_MODE must be one of: "
            f"{ASR_MODE_REALTIME}, {ASR_MODE_REALTIME_DIARIZATION}"
        )
    return configured_mode


def _resolve_tencent_engine_model(asr_mode: str) -> str:
    if asr_mode == ASR_MODE_REALTIME_DIARIZATION:
        return "16k_zh_en_speaker_2.0"
    return "16k_zh"


class Settings:
    app_name: str = os.getenv("APP_NAME", "mediation")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

    asr_mode: str = _resolve_asr_mode()
    tencent_asr_appid: str = os.getenv("TENCENT_ASR_APPID", "")
    tencent_asr_secret_id: str = os.getenv("TENCENT_ASR_SECRET_ID", "")
    tencent_asr_secret_key: str = os.getenv("TENCENT_ASR_SECRET_KEY", "")
    tencent_asr_speaker_diarization: bool = asr_mode == ASR_MODE_REALTIME_DIARIZATION
    tencent_asr_enable_speaker_context: bool = _env_bool("TENCENT_ASR_ENABLE_SPEAKER_CONTEXT")
    tencent_asr_speaker_context_id: str = os.getenv("TENCENT_ASR_SPEAKER_CONTEXT_ID", "")
    tencent_asr_effective_engine_model_type: str = _resolve_tencent_engine_model(asr_mode)

    frontend_dir: Path = BASE_DIR.parent / "frontend"


@lru_cache
def get_settings() -> Settings:
    return Settings()
