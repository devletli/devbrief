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
    # Kanit eslesme esikleri (fact evidence ~ kaynak).
    evidence_token_coverage: float = Field(default=0.85, alias="EVIDENCE_TOKEN_COVERAGE")
    evidence_difflib: float = Field(default=0.9, alias="EVIDENCE_DIFFLIB")
    # Model kapisi (eval-models): voice metninde beklenen min TR harf orani.
    eval_tr_ratio: float = Field(default=0.05, alias="EVAL_TR_RATIO")
    # Ucretli model korumasi: varsayilan kapali, allowlist disindakiler reddedilir.
    allow_paid_models: bool = Field(default=False, alias="ALLOW_PAID_MODELS")
    paid_models_allowlist: str = Field(default="", alias="PAID_MODELS_ALLOWLIST")
    # Dogrulayici modelleri: bos ise SMART kullanilir (writer'dan ayri secilebilir).
    llm_models_verify: str = Field(default="", alias="LLM_MODELS_VERIFY")
    # Yayin modu: shadow (hicbir sey cikmaz) | private (yalnizca ozel YT) | live.
    publish_mode: str = Field(default="shadow", alias="PUBLISH_MODE")
    live_confirm: str = Field(default="", alias="LIVE_CONFIRM")
    # Katman zaman asimlari (sn). Eval + uretim ayni ayari kullanir.
    llm_timeout_fast_s: float = Field(default=60.0, alias="LLM_TIMEOUT_FAST_S")
    llm_timeout_smart_s: float = Field(default=180.0, alias="LLM_TIMEOUT_SMART_S")
    # Gorev cikti sinirlari (token). length kesintisinde ayni model 2x ile 1 kez dener.
    llm_max_tokens_classify: int = Field(default=4096, alias="LLM_MAX_TOKENS_CLASSIFY")
    llm_max_tokens_select: int = Field(default=2048, alias="LLM_MAX_TOKENS_SELECT")
    llm_max_tokens_write_script: int = Field(default=4096, alias="LLM_MAX_TOKENS_WRITE_SCRIPT")
    llm_max_tokens_verify: int = Field(default=2048, alias="LLM_MAX_TOKENS_VERIFY")
    llm_max_tokens_write_metadata: int = Field(
        default=4096, alias="LLM_MAX_TOKENS_WRITE_METADATA")
    llm_max_tokens_repair: int = Field(default=4096, alias="LLM_MAX_TOKENS_REPAIR")
    llm_max_tokens_cap: int = Field(default=8000, alias="LLM_MAX_TOKENS_CAP")
    # Akil yurutme: off (kapat) | low (dusuk efor) | default (karisma).
    llm_reasoning: str = Field(default="default", alias="LLM_REASONING")
    # Probe listesi: bos ise FAST+SMART+VERIFY birlesimi.
    llm_probe_models: str = Field(default="", alias="LLM_PROBE_MODELS")
    # X URL sayimi (t.co): X Counting Characters dokumanina gore her URL
    # 23 sayilir. <=0 ise muhafazakar tam-boy sayima donulur.
    x_url_len: int = Field(default=23, alias="X_URL_LEN")
    # Metadata uretim modu: extractive (havuzdan secim; varsayilan) |
    # generative (serbest cumle; eval-metadata kapisi gecilmeden secilemez).
    metadata_mode: str = Field(default="extractive", alias="METADATA_MODE")

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
    def verify_models(self) -> list[str]:
        custom = [m.strip() for m in self.llm_models_verify.split(",") if m.strip()]
        return custom or self.smart_models

    def timeout_for(self, tier: str) -> float:
        """Katman zaman asimi (sn). Eval + uretim icin TEK kod yolu."""
        return self.llm_timeout_fast_s if tier == "fast" else self.llm_timeout_smart_s

    def max_tokens_for(self, task: str) -> int:
        """Gorev cikti siniri. Bilinmeyen gorevde 1200."""
        return {
            "classify": self.llm_max_tokens_classify,
            "select": self.llm_max_tokens_select,
            "write_script": self.llm_max_tokens_write_script,
            "verify_script": self.llm_max_tokens_verify,
            "write_metadata": self.llm_max_tokens_write_metadata,
            "repair": self.llm_max_tokens_repair,
        }.get(task.split(":")[0], 1200)

    @property
    def probe_models(self) -> list[str]:
        custom = [m.strip() for m in self.llm_probe_models.split(",") if m.strip()]
        if custom:
            return custom
        return list(dict.fromkeys(self.fast_models + self.smart_models + self.verify_models))

    def tier_of(self, model: str) -> str:
        return "fast" if model in self.fast_models else "smart"

    def effective_mode(self) -> tuple[str, str]:
        """(mod, uyari). live icin LIVE_CONFIRM sart, yoksa private'a duser."""
        mode = (self.publish_mode or "shadow").strip().lower()
        if mode not in ("shadow", "private", "live"):
            return "shadow", f"bilinmeyen PUBLISH_MODE={mode!r}, shadow sayildi"
        if mode == "live" and self.live_confirm != "EVET_CANLI":
            return "private", "LIVE_CONFIRM yok, live->private dusuruldu"
        return mode, ""

    @property
    def all_models(self) -> list[str]:
        return self.fast_models + self.smart_models

    @property
    def paid_allowlist(self) -> list[str]:
        return [m.strip() for m in self.paid_models_allowlist.split(",") if m.strip()]

    def model_allowed(self, model: str) -> bool:
        if model.endswith(":free"):
            return True
        return bool(self.allow_paid_models) and model in self.paid_allowlist


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_sources(path: str | Path = "sources.yaml") -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
