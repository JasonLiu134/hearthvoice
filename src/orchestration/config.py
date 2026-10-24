from dataclasses import dataclass
import os


@dataclass
class Settings:
    provider: str = "mock"
    api_token: str = "local-demo-token"
    database_path: str = "data/orchestration.sqlite3"
    scheduler_enabled: bool = False
    public_base_url: str = ""
    voice_url: str = ""
    smoke_test_enabled: bool = False
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    @classmethod
    def from_env(cls):
        settings = cls(
            provider=os.getenv("PROVIDER", "mock"),
            api_token=os.getenv("API_TOKEN", "local-demo-token"),
            database_path=os.getenv("DATABASE_PATH", "data/orchestration.sqlite3"),
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "false").lower() == "true",
            public_base_url=os.getenv("PUBLIC_BASE_URL", ""),
            voice_url=os.getenv("VOICE_URL", ""),
            smoke_test_enabled=os.getenv("SMOKE_TEST_ENABLED", "false").lower() == "true",
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_from_number=os.getenv("TWILIO_FROM_NUMBER") or os.getenv("TWILIO_PHONE_NUMBER", ""),
        )
        settings.validate()
        return settings

    def validate(self):
        if self.provider not in {"mock", "twilio"}:
            raise ValueError("PROVIDER must be mock or twilio")
        if not self.api_token:
            raise ValueError("API_TOKEN must not be empty")
        if self.provider == "twilio":
            if self.api_token == "local-demo-token":
                raise ValueError("Set a private API_TOKEN for Twilio mode")
            if not all((self.twilio_account_sid, self.twilio_auth_token, self.twilio_from_number)):
                raise ValueError("Twilio credentials and from number are required")
            if not all(url.startswith("https://") for url in (self.public_base_url, self.effective_voice_url)):
                raise ValueError("PUBLIC_BASE_URL and VOICE_URL must use HTTPS in Twilio mode")

    @property
    def effective_voice_url(self):
        if self.smoke_test_enabled:
            return self.public_base_url.rstrip("/") + "/webhooks/twilio/smoke/voice"
        return self.voice_url
