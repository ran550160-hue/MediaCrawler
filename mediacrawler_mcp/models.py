from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Dataset:
    dataset_id: str
    name: str
    description: str | None
    status: str
    platforms: list[str]
    keywords: list[str]
    options: dict[str, Any]
    dataset_dir: str
    created_at: str
    updated_at: str
    files: dict[str, Any] = field(default_factory=lambda: {"raw": {}, "reports": {}})
    metrics: dict[str, int] = field(default_factory=lambda: {"content_count": 0, "comment_count": 0})
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
