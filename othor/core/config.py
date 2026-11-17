# SPDX-License-Identifier: Apache-2.0
"""Configuration from environment variables (OTHOR_*) and the data directory.

Nothing reads hard-coded hosts (non-negotiable 7): every endpoint comes from here.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from othor import __version__

DEFAULT_SECRET = "change-me"  # noqa: S105 - the placeholder we refuse in prod


class Settings(BaseSettings):
    # hide_input_in_errors: a settings error never prints the values it read (secrets among
    # them) into a log or a deploy job's output (M11b, seen on the first release)
    model_config = SettingsConfigDict(
        env_prefix="OTHOR_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    env: Literal["dev", "test", "prod"] = "dev"
    base_domain: str = "othor.localhost"
    data_dir: Path = Path("./data")
    db_url: str = "sqlite:///./data/othor.db"
    secret_key: SecretStr = SecretStr(DEFAULT_SECRET)
    instance_key_file: Path | None = None

    mail_relay: Literal["console", "smtp", "postmark", "memory"] = "console"
    smtp_url: str = ""
    mail_from_domain: str = "othor.localhost"
    # Postmark (the cloud relay, decided 6 Nov 2026); on-premise uses SMTP
    postmark_token: SecretStr = SecretStr("")
    postmark_webhook_secret: SecretStr = SecretStr("")
    postmark_stream: str = "outbound"
    # how links in emails reach this instance: <scheme>://<slug>.<base_domain>[:port]
    public_scheme: Literal["http", "https"] = "https"
    public_port: int | None = None

    model_provider: Literal["template", "anthropic", "openai", "local"] = "template"
    model_id: str = ""
    model_api_key: SecretStr = SecretStr("")

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    # Google Picker + drive.file only (docs/05). fixture = the local stand-in under data/sample.
    sheets_mode: Literal["fixture", "google"] = "fixture"
    google_api_key: SecretStr = SecretStr("")
    google_app_id: str = ""

    # the public website on the root host (othor.ai, M11a): on in the cloud, off on-premise
    public_site: bool = False
    # plug-ins (M20.1, v0.20): installed packages in the `othor.plugins` entry-point group load
    # by themselves; this adds "pkg.mod:factory,…" (tests, self-host customisation); "none" = off
    plugins: str = ""
    # one company per install (the open core, owner 16 Nov 2026) or several on subdomains
    # (othor.ai; for self-hosters an enterprise licence feature, M20.3). Unset, it follows the
    # edition (M20.4): the open images (core, ee) run "single", othor.ai and dev "multi"
    tenancy: Literal["single", "multi"] = "multi"
    # the enterprise licence (M20.3): the key's text (a secret store), or a file (default
    # data_dir/licence.key, where Settings › Licence saves a pasted key)
    licence: str = ""
    licence_file: Path | None = None
    # sign-up (M11d part C): `invite` (the cloud; the safe default) needs an invitation from
    # `othor admin invite`; `open` keeps M1's sign-up (dev, tests, on-premise)
    signup: Literal["invite", "open"] = "invite"
    # open sign-up's guard rails (owner, 16 Nov 2026): each company's AI tokens a month (0 = no
    # cap; othor.ai 500000) and the free trial of a company that signs up itself (0 = none;
    # othor.ai 14 days, then closed and purged 30 days later)
    ai_monthly_tokens: int = 0
    trial_days: int = 0
    # the source-host guard (owner, 16 Nov 2026; othor.ai only): refuse a PostgreSQL/MySQL host
    # on a private network or a sample service name, at save and on every connection; the allow
    # list names companies (slugs) it skips. Off for self-hosters (their databases are private)
    source_host_guard: bool = False
    source_host_allow: str = ""
    # where Request access forms are emailed (M11d part C)
    access_request_to: str = "support@othor.ai"
    # outward links (M20.2, owner 16 Nov 2026): Learn links go to othor.ai's public docs; the
    # Terms/Privacy line and "write to support" show only when an install sets them (othor.ai
    # sets its own; a self-hoster may set theirs)
    help_url: str = "https://othor.ai"
    legal_url: str = ""
    support_email: str = ""
    # the proxy in front of the app, trusted for the client IP (rate limits) and, with `alb`,
    # the scheme: none = the socket; fly = Fly-Client-IP; alb = the last X-Forwarded-For hop
    # (the one the AWS load balancer appends) and X-Forwarded-Proto (M11b)
    # the proxy in front that sets X-Forwarded-*: AWS's load balancer, or Caddy in the self-host
    # compose (M20.4); both append the address they saw, so the last one is the client
    trust_proxy: Literal["none", "fly", "alb", "caddy"] = "none"
    # which image this is (M20.4): core (othor) · ee (othor-ee) · hosted (othor.ai) · dev
    edition: Literal["core", "ee", "hosted", "dev"] = "dev"
    # the worker watched from outside it (M11b §6): check-in URLs (Healthchecks.io or a
    # customer's own), pinged with an empty GET; empty = off, the on-premise default
    heartbeat_url: SecretStr = SecretStr("")
    nightly_ping_url: SecretStr = SecretStr("")
    # who hears that a night failed or ran late (ops alerts, M11b §6); empty = off
    ops_email: str = ""

    egress_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ips_url: str = "https://othor.ai/ips"
    default_ssl: Literal["require", "verify-ca", "disable"] = "require"
    materiality_floor_inr: int = 500_000
    # the ceiling on metrics in use per company (M15.1; measured at 60, docs/plans/M15.1.md)
    max_metrics: int = 60
    # recorded on every run and in its hash (docs/02 "Determinism"); the image sets it at build
    image_version: str = __version__
    fdr_q: Decimal = Decimal("0.01")

    @field_validator("egress_ips", mode="before")
    @classmethod
    def _split_ips(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @model_validator(mode="after")
    def _tenancy_follows_edition(self) -> Self:
        if "tenancy" not in self.model_fields_set and self.edition in ("core", "ee"):
            self.tenancy = "single"
        return self

    @model_validator(mode="after")
    def _prod_needs_real_secret(self) -> Self:
        if self.env == "prod" and self.secret_key.get_secret_value() in ("", DEFAULT_SECRET):
            raise ValueError("OTHOR_SECRET_KEY must be set to a real secret in prod")
        if self.mail_relay == "postmark" and not (
            self.postmark_token.get_secret_value()
            and self.postmark_webhook_secret.get_secret_value()
        ):
            raise ValueError(
                "OTHOR_MAIL_RELAY=postmark needs OTHOR_POSTMARK_TOKEN and "
                "OTHOR_POSTMARK_WEBHOOK_SECRET"
            )
        return self

    def instance_url(self, slug: str | None, path: str = "/") -> str:
        """An absolute link to this instance for emails (no request to take the host from)."""
        multi = slug and self.tenancy == "multi"  # one company per install: no slug (M20.1)
        host = f"{slug}.{self.base_domain}" if multi else self.base_domain
        port = f":{self.public_port}" if self.public_port else ""
        return f"{self.public_scheme}://{host}{port}{path}"

    @property
    def licence_path(self) -> Path:
        return self.licence_file or self.data_dir / "licence.key"

    # Data-directory layout (docs/02 "Storage").
    @property
    def instance_key_path(self) -> Path:
        return self.instance_key_file or self.data_dir / "instance.key"

    def company_dir(self, company_id: uuid.UUID) -> Path:
        return self.data_dir / "companies" / str(company_id)

    def analytics_path(self, company_id: uuid.UUID) -> Path:
        return self.company_dir(company_id) / "analytics.duckdb"

    def uploads_dir(self, company_id: uuid.UUID) -> Path:
        return self.company_dir(company_id) / "uploads"

    def runs_dir(self, company_id: uuid.UUID) -> Path:
        return self.company_dir(company_id) / "runs"

    @property
    def sample_dir(self) -> Path:
        """Dev only: the Sheets API stand-in written by `othor seed`."""
        return self.data_dir / "sample"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
