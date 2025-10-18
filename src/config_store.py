from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

APP_NAME = "jellyfin-gtk"


@dataclass
class AppConfig:
    server_url: str
    access_token: str
    user_id: str
    username: Optional[str] = None


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def _cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".cache" / APP_NAME


def ensure_dirs() -> tuple[Path, Path]:
    cdir = _config_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "images").mkdir(parents=True, exist_ok=True)
    return cdir, cache


def config_path() -> Path:
    cdir, _ = ensure_dirs()
    return cdir / "config.json"


def cache_dir() -> Path:
    _, c = ensure_dirs()
    return c


def load_config() -> Optional[AppConfig]:
    path = config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return AppConfig(
            server_url=data.get("server_url", ""),
            access_token=data.get("access_token", ""),
            user_id=data.get("user_id", ""),
        )
    except Exception:
        return None
    
def clear_config() -> None:
    path = config_path()
    if path.exists():
        path.unlink()

def clear_cache() -> None:
    cdir = cache_dir()
    for item in cdir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            for subitem in item.iterdir():
                if subitem.is_file():
                    subitem.unlink()
            item.rmdir()

def clear_cache_excluding_images() -> None:
    cdir = cache_dir()
    for item in cdir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir() and item.name != "images":
            for subitem in item.iterdir():
                if subitem.is_file():
                    subitem.unlink()
            item.rmdir()


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    path.write_text(json.dumps(asdict(cfg), indent=2))


def cache_path(name: str) -> Path:
    return cache_dir() / name
