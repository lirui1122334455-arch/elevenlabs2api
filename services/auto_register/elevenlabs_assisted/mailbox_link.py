from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlsplit


_TEXT_URL = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_TEXT_FIELDS = ("text", "raw", "content", "intro", "body", "snippet", "source")


@dataclass(frozen=True)
class MailContent:
    message_id: str
    subject: str
    text: str
    html: tuple[str, ...]


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.urls.append(unescape(value.strip()))


def mail_content_from_item(item: dict[str, Any]) -> MailContent:
    message_id = str(item.get("id") or item.get("msgid") or item.get("message_id") or "")
    subject = str(item.get("subject") or "")
    text_parts: list[str] = []
    for field in _TEXT_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            text_parts.append(value)
    raw_html = item.get("html") or []
    if isinstance(raw_html, str):
        raw_html = [raw_html]
    html_parts = tuple(value for value in raw_html if isinstance(value, str) and value.strip())
    return MailContent(message_id, subject, "\n".join(text_parts), html_parts)


def merge_mail_content(primary: MailContent, detail: MailContent) -> MailContent:
    return MailContent(
        primary.message_id or detail.message_id,
        primary.subject or detail.subject,
        "\n".join(part for part in (primary.text, detail.text) if part),
        primary.html + detail.html,
    )


def normalize_allowed_hosts(values: Iterable[str]) -> frozenset[str]:
    hosts: set[str] = set()
    for value in values:
        host = str(value or "").strip().lower().rstrip(".")
        if host:
            hosts.add(host)
    if not hosts:
        raise ValueError("at least one verification host is required")
    return frozenset(hosts)


def _host_allowed(host: str, allowed_hosts: frozenset[str]) -> bool:
    normalized = host.lower().rstrip(".")
    for allowed in allowed_hosts:
        if allowed.startswith("*."):
            suffix = allowed[1:]
            if normalized.endswith(suffix) and normalized != suffix[1:]:
                return True
        elif normalized == allowed:
            return True
    return False


def is_elevenlabs_verification_url(url: str, allowed_hosts: Iterable[str]) -> bool:
    try:
        parsed = urlsplit(unescape(url.strip()))
        query = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        return False
    hosts = normalize_allowed_hosts(allowed_hosts)
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and _host_allowed(parsed.hostname or "", hosts)
        and query.get("mode") == ["verifyEmail"]
        and bool(query.get("oobCode") or query.get("verificationCode"))
    )


def verification_action_code(url: str) -> str:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    values = query.get("oobCode") or query.get("verificationCode") or []
    return str(values[0]) if values else ""


def candidate_urls(content: MailContent) -> list[str]:
    values: list[str] = []
    for chunk in content.html:
        parser = _HrefParser()
        try:
            parser.feed(chunk)
        except Exception:
            pass
        values.extend(parser.urls)
    for chunk in (content.subject, content.text):
        values.extend(unescape(match.group(0)).rstrip(".,;:!?)]}") for match in _TEXT_URL.finditer(chunk))
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def extract_verification_link(content: MailContent, allowed_hosts: Iterable[str]) -> str:
    hosts = normalize_allowed_hosts(allowed_hosts)
    for url in candidate_urls(content):
        if is_elevenlabs_verification_url(url, hosts):
            return unescape(url.strip())
    return ""


def redacted_link_label(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"


def _phase(emit: Callable[[str], None] | None, message: str) -> None:
    if emit:
        emit(f"[phase:wait_email_link] {message}")


def wait_for_cloudflare_verification_link(
    receiver: Any,
    *,
    timeout: float,
    allowed_hosts: Iterable[str],
    emit: Callable[[str], None] | None = None,
) -> str:
    from protocol_register import _cf_headers, _cf_path, _cf_pick_list, _mail_session

    if not receiver.api_base:
        raise RuntimeError("cloudflare_api_base is required")
    hosts = normalize_allowed_hosts(allowed_hosts)
    deadline = time.time() + timeout
    poll = max(0.5, min(float(receiver.config.get("mail_poll_interval") or 2), 5.0))
    path = _cf_path(receiver.config, "cloudflare_path_messages", "/api/mails")
    attempts: dict[str, int] = {}
    consumed_codes = getattr(receiver, "_consumed_verification_codes", set())
    receiver._consumed_verification_codes = consumed_codes
    last_report = 0.0
    while time.time() < deadline:
        if emit and time.time() - last_report >= 15:
            _phase(emit, f"polling inbox remaining={int(max(0, deadline - time.time()))}s email={receiver.email}")
            last_report = time.time()
        try:
            response = _mail_session(receiver.config).get(
                f"{receiver.api_base}{path}",
                headers=_cf_headers(receiver.config, bearer=receiver.jwt),
                params={"limit": 20, "offset": 0},
                timeout=20,
            )
            response.raise_for_status()
            messages = _cf_pick_list(response.json())
        except Exception:
            time.sleep(poll)
            continue
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            content = mail_content_from_item(raw)
            if content.message_id and content.message_id in receiver._consumed_message_ids:
                continue
            link = extract_verification_link(content, hosts)
            if not link and content.message_id and attempts.get(content.message_id, 0) < 8:
                attempts[content.message_id] = attempts.get(content.message_id, 0) + 1
                for detail_url in (
                    f"{receiver.api_base}/api/mail/{content.message_id}",
                    f"{receiver.api_base}{path}/{content.message_id}",
                ):
                    try:
                        detail_response = _mail_session(receiver.config).get(
                            detail_url,
                            headers=_cf_headers(receiver.config, bearer=receiver.jwt),
                            timeout=20,
                        )
                        if not detail_response.ok:
                            continue
                        detail = detail_response.json()
                        if isinstance(detail, dict) and isinstance(detail.get("data"), dict):
                            detail = detail["data"]
                        if isinstance(detail, dict):
                            content = merge_mail_content(content, mail_content_from_item(detail))
                            link = extract_verification_link(content, hosts)
                        if link:
                            break
                    except Exception:
                        continue
            code = verification_action_code(link) if link else ""
            if link and code not in consumed_codes:
                if content.message_id:
                    receiver._consumed_message_ids.add(content.message_id)
                consumed_codes.add(code)
                return link
        time.sleep(poll)
    raise RuntimeError("timeout waiting for ElevenLabs verification link")


def wait_for_yyds_verification_link(
    receiver: Any,
    *,
    timeout: float,
    allowed_hosts: Iterable[str],
    emit: Callable[[str], None] | None = None,
) -> str:
    from protocol_register import _mail_session, _yyds_auth_headers, _yyds_unwrap

    hosts = normalize_allowed_hosts(allowed_hosts)
    deadline = time.time() + timeout
    poll = max(0.5, min(float(receiver.config.get("mail_poll_interval") or 2), 5.0))
    attempts: dict[str, int] = {}
    consumed_codes = getattr(receiver, "_consumed_verification_codes", set())
    receiver._consumed_verification_codes = consumed_codes
    last_report = 0.0
    while time.time() < deadline:
        remaining = max(1.0, deadline - time.time())
        wait_sec = int(min(30, remaining))
        if emit and time.time() - last_report >= 15:
            _phase(emit, f"yyds polling remaining={int(remaining)}s email={receiver.email}")
            last_report = time.time()
        messages: list[dict[str, Any]] = []
        try:
            response = _mail_session(receiver.config).get(
                f"{receiver.api_base}/messages/next",
                headers=_yyds_auth_headers(receiver.config, temp_token=receiver.temp_token),
                params={"address": receiver.email, "wait": wait_sec},
                timeout=wait_sec + 15,
            )
            if response.ok and response.status_code != 204:
                payload = _yyds_unwrap(response.json())
                if isinstance(payload, dict):
                    message = payload.get("message") or payload
                    if isinstance(message, dict):
                        messages.append(message)
        except Exception:
            pass
        if not messages:
            try:
                response = _mail_session(receiver.config).get(
                    f"{receiver.api_base}/messages",
                    headers=_yyds_auth_headers(receiver.config, temp_token=receiver.temp_token),
                    params={"address": receiver.email},
                    timeout=20,
                )
                if response.ok:
                    payload = _yyds_unwrap(response.json())
                    if isinstance(payload, list):
                        messages = [item for item in payload if isinstance(item, dict)]
                    elif isinstance(payload, dict):
                        values = payload.get("messages") or payload.get("items") or []
                        messages = [item for item in values if isinstance(item, dict)]
            except Exception:
                pass
        for item in messages:
            content = mail_content_from_item(item)
            link = extract_verification_link(content, hosts)
            if not link and content.message_id and attempts.get(content.message_id, 0) < 8:
                attempts[content.message_id] = attempts.get(content.message_id, 0) + 1
                try:
                    detail_response = _mail_session(receiver.config).get(
                        f"{receiver.api_base}/messages/{content.message_id}",
                        headers=_yyds_auth_headers(receiver.config, temp_token=receiver.temp_token),
                        params={"address": receiver.email},
                        timeout=20,
                    )
                    if detail_response.ok:
                        detail = _yyds_unwrap(detail_response.json())
                        if isinstance(detail, dict):
                            content = merge_mail_content(content, mail_content_from_item(detail))
                            link = extract_verification_link(content, hosts)
                except Exception:
                    pass
            code = verification_action_code(link) if link else ""
            if link and code not in consumed_codes:
                consumed_codes.add(code)
                return link
        if not messages:
            time.sleep(poll)
    raise RuntimeError("timeout waiting for ElevenLabs verification link (YYDS)")
