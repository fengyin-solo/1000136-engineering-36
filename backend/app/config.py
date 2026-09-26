"""运行配置：端口、跨域、运行环境与启动数据准备路径。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_BACKEND_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = "实验室样品检测管理平台"
    env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "local"))
    port: int = field(default_factory=lambda: _env_int("APP_PORT", 8000))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200
    # 体系文档启动依赖：源文件目录、构建缓存目录与缓存有效期（秒，<=0 表示永不过期）
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("APP_DATA_DIR", str(_BACKEND_ROOT / "data"))
        )
    )
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("APP_CACHE_DIR", str(_BACKEND_ROOT / ".cache"))
        )
    )
    document_cache_ttl_seconds: int = field(
        default_factory=lambda: _env_int("DOCUMENT_CACHE_TTL_SECONDS", 24 * 3600)
    )


settings = Settings()
