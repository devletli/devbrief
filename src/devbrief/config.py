"""Uygulama yapilandirmasi: .env + sources.yaml okur. Secrets asla loglanmaz."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    llm_models_fast: str = Field(default="", alias="LLM_MODELS_FAST")
    llm_models_smart: str = Field(default="", alias="LLM_MODELS_SMART")
    llm_daily_budget: int = Field(default=400, alias="LLM_DAILY_BUDGET")
    llm_rate_per_minute: int = Field(default=15, alias="LLM_RATE_PER_MINUTE")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    tts_voice: str = Field(default="tr-TR-AhmetNeural", alias="TTS_VOICE")
    ffmpeg_path: str = Field(default="", alias="FFMPEG_PATH")
    ffprobe_path: str = Field(default="", alias="FFPROBE_PATH")

    # Prefilter katsayilari (docs/02 §6). Haftalik elle ayarlanir.
    prefilter_w_source: float = Field(default=1.0, alias="PREFILTER_W_SOURCE")
    prefilter_w_pop: float = Field(default=0.5, alias="PREFILTER_W_POP")
    prefilter_w_fresh: float = Field(default=1.0, alias="PREFILTER_W_FRESH")
    prefilter_w_kw: float = Field(default=1.0, alias="PREFILTER_W_KW")
    # Kaynak basina kota + toplam aday sayisi; HN dominansini keser.
    prefilter_per_source_cap: int = Field(default=10, alias="PREFILTER_PER_SOURCE_CAP")
    prefilter_total: int = Field(default=40, alias="PREFILTER_TOTAL")
    prefilter_terms: str = Field(
        default="config/prefilter_terms.yaml", alias="PREFILTER_TERMS"
    )
    # Dedupe esikleri (0-1).
    dedupe_jaccard: float = Field(default=0.5, alias="DEDUPE_JACCARD")
    dedupe_difflib: float = Field(default=0.8, alias="DEDUPE_DIFFLIB")
    dedupe_max_size: int = Field(default=6, alias="DEDUPE_MAX_SIZE")
    # on_screen kanca/voice tekrar esigi (difflib orani; uzeri uyari, ret degil).
    onscreen_repeat_thr: float = Field(default=0.8, alias="ONSCREEN_REPEAT_THR")
    # Model kapisi (eval-models): voice metninde beklenen min TR harf orani.
    eval_tr_ratio: float = Field(default=0.05, alias="EVAL_TR_RATIO")

    devbrief_api_token: str = Field(default="", alias="DEVBRIEF_API_TOKEN")
    n8n_callback_url: str = Field(
        default="http://n8n:5678/webhook/devbrief-run-finished", alias="N8N_CALLBACK_URL"
    )
    n8n_callback_secret: str = Field(default="", alias="N8N_CALLBACK_SECRET")
    n8n_encryption_key: str = Field(default="", alias="N8N_ENCRYPTION_KEY")

    @field_validator("llm_models_fast", "llm_models_smart", mode="before")
    @classmethod
    def _empty_to_str(cls, v: object) -> str:
        return "" if v is None else str(v)

    @property
    def fast_models(self) -> list[str]:
        return [m.strip() for m in self.llm_models_fast.split(",") if m.strip()]

    @property
    def smart_models(self) -> list[str]:
        return [m.strip() for m in self.llm_models_smart.split(",") if m.strip()]

    @property
    def all_models(self) -> list[str]:
        return self.fast_models + self.smart_models


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_sources(path: str | Path = "sources.yaml") -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
