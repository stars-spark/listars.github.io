"""所有配置都从环境变量（或 .env 文件）读取，集中在这里。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if not v else v.lower() in ("1", "true", "yes", "on")


# 各家模型的预设：DeepSeek 与其他 OpenAI 兼容接口共用一套调用代码
PROVIDER_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    "claude": {"base_url": "", "model": "claude-opus-5", "key_env": "ANTHROPIC_API_KEY"},
    "openai": {"base_url": "", "model": "", "key_env": "LLM_API_KEY"},
}


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    max_tokens: int = 8192
    effort: str = "high"  # 仅 Claude 使用

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = _env("LLM_PROVIDER", "deepseek").lower()
        if provider not in PROVIDER_PRESETS:
            raise ValueError(f"LLM_PROVIDER 只支持 {', '.join(PROVIDER_PRESETS)}，当前是 {provider}")
        preset = PROVIDER_PRESETS[provider]
        return cls(
            provider=provider,
            model=_env("LLM_MODEL", preset["model"]),
            api_key=_env("LLM_API_KEY") or _env(preset["key_env"]),
            base_url=_env("LLM_BASE_URL", preset["base_url"]).rstrip("/"),
            max_tokens=int(_env("LLM_MAX_TOKENS", "64000" if provider == "claude" else "8192")),
            effort=_env("LLM_EFFORT", "high"),
        )


@dataclass
class Settings:
    data_dir: Path = Path("data")
    bili_cookie: str = ""
    fav_folder: str = ""  # 收藏夹名称、media_id 或收藏夹链接
    sync_watchlater: bool = False
    sync_interval: int = 10  # 分钟
    initial_sync_limit: int = 10  # 第一次同步只取最近收藏的 N 个，防止一次性跑完整个大收藏夹
    max_parts: int = 10  # 多 P 视频最多整理多少 P
    asr_enabled: bool = True
    whisper_model: str = "small"
    app_password: str = ""
    public_url: str = ""
    bark_key: str = ""
    bark_server: str = "https://api.day.app"
    serverchan_key: str = ""
    pushplus_token: str = ""
    prompt_file: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bili-notes.db"

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / "notes"

    def note_url(self, video_id: int) -> str:
        return f"{self.public_url.rstrip('/')}/v/{video_id}" if self.public_url else ""

    @classmethod
    def from_env(cls) -> "Settings":
        prompt = _env("PROMPT_FILE")
        return cls(
            data_dir=Path(_env("DATA_DIR", "data")),
            bili_cookie=_env("BILI_COOKIE"),
            fav_folder=_env("BILI_FAV_FOLDER"),
            sync_watchlater=_bool("BILI_SYNC_WATCHLATER", False),
            sync_interval=max(1, int(_env("SYNC_INTERVAL_MINUTES", "10"))),
            initial_sync_limit=int(_env("INITIAL_SYNC_LIMIT", "10")),
            max_parts=int(_env("MAX_PARTS", "10")),
            asr_enabled=_bool("ASR_ENABLED", True),
            whisper_model=_env("WHISPER_MODEL", "small"),
            app_password=_env("APP_PASSWORD"),
            public_url=_env("PUBLIC_URL"),
            bark_key=_env("BARK_KEY"),
            bark_server=_env("BARK_SERVER", "https://api.day.app").rstrip("/"),
            serverchan_key=_env("SERVERCHAN_KEY"),
            pushplus_token=_env("PUSHPLUS_TOKEN"),
            prompt_file=Path(prompt) if prompt else None,
        )
