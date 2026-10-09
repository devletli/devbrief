"""Curate semalari: classify_batch + select_daily ciktilari (pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["release", "security", "tooling", "ai", "cloud", "language", "research", "other"]


class ClassifiedItem(BaseModel):
    id: int
    category: Category = "other"
    importance: int = Field(ge=1, le=5)
    affects_devs: bool = False
    reason: str = Field(max_length=200)


class ClassifyBatchOut(BaseModel):
    results: list[ClassifiedItem]


class Pick(BaseModel):
    cluster_id: int
    merged_cluster_ids: list[int] = Field(default_factory=list)
    why_today: str = Field(min_length=1)


class SelectDailyOut(BaseModel):
    picks: list[Pick] = Field(max_length=3)
    backup: Pick
