from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .batch import parse_proxy_urls
from .dynamic_proxy import is_dynamic_proxy_api
from .mailbox_link import normalize_allowed_hosts
from .passwords import is_valid_password
from .proxy_preflight import normalize_proxy_url


DEFAULT_VERIFICATION_HOSTS = (
    "elevenlabs.io",
    "*.elevenlabs.io",
    "elevenlabs.firebaseapp.com",
)
_ENV_VALUE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_CAPTCHA_PROVIDERS = {"yescaptcha", "captcha_gateway"}


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _timeout(value: Any, default: float, name: str, *, maximum: float = 3600) -> float:
    result = float(value if value not in {None, ""} else default)
    if result <= 0 or result > maximum:
        raise ValueError(f"{name} must be between 0 and {maximum} seconds")
    return result


def _host_values(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return DEFAULT_VERIFICATION_HOSTS
    if isinstance(value, str):
        return tuple(part.strip() for part in value.replace(";", ",").split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    raise ValueError("mail.allowed_verification_hosts must be a string or list")


def _placeholder(value: Any) -> bool:
    return str(value or "").strip().lower().startswith("replace-with-")


def _expand_env_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _ENV_VALUE.fullmatch(value.strip())
    return os.getenv(match.group(1), "") if match else value


def _captcha_provider(value: Any) -> str:
    provider = str(value or "yescaptcha").strip().lower().replace("-", "_")
    if provider == "gateway":
        provider = "captcha_gateway"
    if provider not in _CAPTCHA_PROVIDERS:
        raise ValueError("captcha.provider must be yescaptcha or captcha_gateway")
    return provider


@dataclass(frozen=True)
class ElevenLabsConfig:
    source: Path
    proxy_url: str
    proxy_pool: tuple[str, ...]
    dynamic_proxy_api: str
    preflight_url: str
    proxy_timeout: float
    profile_dir: Path
    unique_profile: bool
    headless: bool
    browser_channel: str
    navigation_timeout: float
    mail_timeout: float
    confirmation_timeout: float
    captcha_timeout: float
    captcha_provider: str
    captcha_api_key: str
    captcha_endpoint: str
    allowed_verification_hosts: tuple[str, ...]
    mail: dict[str, Any]
    email: str
    password: str
    save_credentials: bool
    credentials_file: Path

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "ElevenLabsConfig":
        source = Path(path).expanduser().resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("config root must be an object")
        provider = str(payload.get("provider") or "elevenlabs").strip().lower()
        if provider not in {"elevenlabs", "elevenlabs_assisted"}:
            raise ValueError("provider must be elevenlabs")

        proxy = _mapping(payload.get("proxy"), "proxy")
        dynamic_proxy_api = str(
            proxy.get("api_url")
            or os.getenv("ELEVENLABS_DYNAMIC_PROXY_API")
            or ""
        ).strip()
        if dynamic_proxy_api and not is_dynamic_proxy_api(dynamic_proxy_api):
            raise ValueError("proxy.api_url must be an HTTPS dynamic-IP API")
        proxy_pool = () if dynamic_proxy_api else parse_proxy_urls(
            proxy.get("urls") or proxy.get("pool"),
            proxy.get("url"),
            os.getenv("ELEVENLABS_PROXY_URLS") or os.getenv("ELEVENLABS_PROXY_URL") or "",
        )
        proxy_url = "" if dynamic_proxy_api else normalize_proxy_url(str(proxy.get("url") or ""))
        if not proxy_url and len(proxy_pool) == 1:
            proxy_url = proxy_pool[0]
        if proxy.get("require_proxy") is True and not proxy_pool and not dynamic_proxy_api:
            raise ValueError("proxy.url is required when proxy.require_proxy is true")

        browser = _mapping(payload.get("browser"), "browser")
        headless = browser.get("headless", False)
        if not isinstance(headless, bool):
            raise ValueError("browser.headless must be a boolean")
        browser_channel = str(
            browser.get("channel")
            if browser.get("channel") is not None
            else os.getenv("ELEVENLABS_BROWSER_CHANNEL", "chrome")
        ).strip()
        profile_value = str(browser.get("profile_dir") or "./data/elevenlabs-profile")
        profile_dir = Path(profile_value).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = source.parent / profile_dir
        unique_profile = browser.get("unique_profile")
        if unique_profile is None:
            unique_profile = True
        if not isinstance(unique_profile, bool):
            raise ValueError("browser.unique_profile must be a boolean")
        profile_dir = profile_dir.resolve()
        if os.name == "nt":
            daily_profile = (Path(os.getenv("LOCALAPPDATA") or "") / "Google/Chrome/User Data").resolve()
            try:
                profile_dir.relative_to(daily_profile)
            except ValueError:
                pass
            else:
                raise ValueError("browser.profile_dir must not use the daily Chrome profile")

        mail = _mapping(payload.get("mail"), "mail")
        for key in (
            "yyds_api_key",
            "yyds_jwt",
            "cloudflare_api_key",
            "mail_admin_key",
            "mailDomains",
            "mail_domains",
        ):
            if key in mail:
                mail[key] = _expand_env_value(mail[key])
        if mail.get("provider") and not mail.get("email_provider"):
            mail["email_provider"] = str(mail["provider"])
        allowed_hosts = _host_values(mail.get("allowed_verification_hosts"))
        normalize_allowed_hosts(allowed_hosts)
        email_proxy = str(mail.get("email_proxy") or "same_as_browser").strip()
        mail["email_proxy"] = (proxy_url or "direct") if email_proxy == "same_as_browser" else email_proxy

        account = _mapping(payload.get("account"), "account")
        email = str(account.get("email") or "auto").strip()
        if email.lower() == "auto":
            email = ""
        elif "@" not in email:
            raise ValueError("account.email is invalid")
        password = str(account.get("password") or "auto")
        if password.lower() == "auto":
            password = ""
        elif not is_valid_password(password):
            raise ValueError("account.password does not meet ElevenLabs requirements")

        captcha = _mapping(payload.get("captcha"), "captcha")
        captcha_provider = _captcha_provider(
            captcha.get("provider") or os.getenv("ELEVENLABS_CAPTCHA_PROVIDER")
        )
        if captcha_provider == "captcha_gateway":
            captcha_key_env = os.getenv("CAPTCHA_GATEWAY_API_KEY") or ""
            captcha_endpoint_env = os.getenv("CAPTCHA_GATEWAY_ENDPOINT") or ""
            captcha_endpoint_default = "https://sub.aixiangshu.com"
        else:
            captcha_key_env = (
                os.getenv("YESCAPTCHA_API_KEY")
                or os.getenv("GROK2API_YESCAPTCHA_KEY")
                or ""
            )
            captcha_endpoint_env = (
                os.getenv("GROK2API_YESCAPTCHA_ENDPOINT")
                or os.getenv("YESCAPTCHA_ENDPOINT")
                or ""
            )
            captcha_endpoint_default = "https://api.yescaptcha.com"
        captcha_api_key = str(
            _expand_env_value(captcha.get("api_key") or "")
            or captcha_key_env
            or ""
        ).strip()
        captcha_endpoint = str(
            _expand_env_value(
                captcha.get("endpoint")
                or captcha_endpoint_env
                or captcha_endpoint_default
            )
        ).strip()
        save_credentials = account.get("save_credentials")
        if save_credentials is None:
            save_credentials = True
        if not isinstance(save_credentials, bool):
            raise ValueError("account.save_credentials must be a boolean")
        credentials_value = str(account.get("credentials_file") or "./data/elevenlabs-credentials.json")
        credentials_file = Path(credentials_value).expanduser()
        if not credentials_file.is_absolute():
            credentials_file = source.parent / credentials_file

        return cls(
            source=source,
            proxy_url=proxy_url,
            proxy_pool=proxy_pool,
            dynamic_proxy_api=dynamic_proxy_api,
            preflight_url=str(proxy.get("preflight_url") or "https://elevenlabs.io/app/sign-up"),
            proxy_timeout=_timeout(proxy.get("connect_timeout_sec"), 15, "proxy.connect_timeout_sec", maximum=120),
            profile_dir=profile_dir,
            unique_profile=unique_profile,
            headless=headless,
            browser_channel=browser_channel,
            navigation_timeout=_timeout(browser.get("navigation_timeout_sec"), 60, "browser.navigation_timeout_sec"),
            mail_timeout=_timeout(mail.get("timeout_sec"), 180, "mail.timeout_sec"),
            confirmation_timeout=_timeout(
                browser.get("user_confirmation_timeout_sec"),
                180,
                "browser.user_confirmation_timeout_sec",
            ),
            captcha_timeout=_timeout(
                captcha.get("timeout_sec") or browser.get("captcha_timeout_sec"),
                180,
                "captcha.timeout_sec",
            ),
            captcha_provider=captcha_provider,
            captcha_api_key=captcha_api_key,
            captcha_endpoint=captcha_endpoint,
            allowed_verification_hosts=allowed_hosts,
            mail=mail,
            email=email,
            password=password,
            save_credentials=save_credentials,
            credentials_file=credentials_file.resolve(),
        )

    def validate_for_run(self) -> None:
        if not self.email:
            provider = str(
                self.mail.get("email_provider")
                or self.mail.get("provider")
                or self.mail.get("mail_provider")
                or ""
            ).strip()
            if not provider:
                raise ValueError("mail.provider is required when account.email is auto")
            if provider.lower() in {"yyds", "yyds_mail", "215", "215.im"}:
                key = self.mail.get("yyds_api_key") or self.mail.get("yyds_jwt")
                if not key or _placeholder(key):
                    raise ValueError("replace the local YYDS API key/JWT before running")
                domains = self.mail.get("mailDomains") or self.mail.get("mail_domains")
                if _placeholder(domains):
                    raise ValueError("replace the local owned mail domain before running")
            else:
                api_base = self.mail.get("cloudflare_api_base") or self.mail.get("mail_api_base")
                if not api_base or _placeholder(api_base):
                    raise ValueError("replace the local Cloud Temp Mail API base before running")

    def validate_for_automated_run(self) -> None:
        self.validate_for_run()
        if not self.captcha_api_key or _placeholder(self.captcha_api_key):
            label = "Captcha Gateway" if self.captcha_provider == "captcha_gateway" else "YesCaptcha"
            raise ValueError(f"replace the local {label} API key before running automated signup")
        if self.captcha_provider == "captcha_gateway" and not (self.proxy_url or self.proxy_pool or self.dynamic_proxy_api):
            raise ValueError(
                "Captcha Gateway hCaptcha requires proxy.url; "
                "configure a task proxy or select YesCaptcha for direct mode"
            )

    def with_proxy(self, proxy_url: str) -> "ElevenLabsConfig":
        mail = dict(self.mail)
        email_proxy = str(mail.get("email_proxy") or "same_as_browser").strip()
        if email_proxy in {"same_as_browser", "direct", ""} or email_proxy == (self.proxy_url or "direct"):
            mail["email_proxy"] = proxy_url or "direct"
        return replace(self, proxy_url=proxy_url, mail=mail)
