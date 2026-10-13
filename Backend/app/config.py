"""Settings read from environment variables (or Backend/.env)."""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL", "postgresql://sass:change-me@localhost:5432/sass"
        )
        self.agentic_base_url: str = os.getenv("AGENTIC_BASE_URL", "http://localhost:8000")
        self.agentic_api_token: str = os.getenv("AGENTIC_API_TOKEN", "")
        self.webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
        # Comma-separated list of frontend origins allowed to call this API.
        self.cors_origins: list[str] = [
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
            if o.strip()
        ]


settings = Settings()
