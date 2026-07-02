from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class Settings:
    database_dir: Path = Path(
        os.getenv("SCANNER_DATABASE_DIR", PROJECT_ROOT / "database")
    )
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("SCANNER_CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ) or ("*",)
    default_limit: int = int(os.getenv("SCANNER_DEFAULT_LIMIT", "100"))
    max_limit: int = int(os.getenv("SCANNER_MAX_LIMIT", "500"))
