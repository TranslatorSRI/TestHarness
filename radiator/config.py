"""Settings, all from the environment so they can come from a k8s secret."""

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """A required setting is missing."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Bearer token for the API, which the harness uses to upload.
    api_token: str
    # The one shared login for the consortium.
    username: str
    password: str
    # Signs the session cookie. Changing it logs everyone out.
    session_secret: str
    # Only send the session cookie over HTTPS. Off just for local development.
    secure_cookies: bool = True
    # Environments the run grid greys out and gives no trend: their runs are
    # shown, but don't count. Comma-separated in RADIATOR_EXCLUDED_ENVS.
    excluded_envs: tuple[str, ...] = ("dev",)

    @classmethod
    def from_env(cls) -> "Settings":
        def required(name):
            value = os.getenv(name)
            if not value:
                raise ConfigError(f"{name} must be set")
            return value

        return cls(
            database_url=required("RADIATOR_DATABASE_URL"),
            api_token=required("RADIATOR_API_TOKEN"),
            username=required("RADIATOR_USERNAME"),
            password=required("RADIATOR_PASSWORD"),
            session_secret=required("RADIATOR_SESSION_SECRET"),
            secure_cookies=os.getenv("RADIATOR_SECURE_COOKIES", "true").lower()
            not in ("0", "false", "no"),
            excluded_envs=tuple(
                env.strip().lower()
                for env in os.getenv("RADIATOR_EXCLUDED_ENVS", "dev").split(",")
                if env.strip()
            ),
        )
