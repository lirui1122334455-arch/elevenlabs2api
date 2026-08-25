from __future__ import annotations

import email as email_lib
import imaplib
import json
import sqlite3
import threading
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable
import re

from .mailbox_link import (
    MailContent,
    extract_verification_link,
    mail_content_from_item,
    merge_mail_content,
)


TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
IMAP_HOST = "outlook.office365.com"
DEFAULT_FOLDERS = ("INBOX", "Junk", "Junk Email", "Spam")
_STORE_LOCK = threading.RLock()


class FatalOutlookMailError(RuntimeError):
    """Outlook credentials or IMAP authentication cannot be recovered."""


def parse_outlook_lines(text: str) -> list[dict[str, str]]:
    accounts: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("----")]
        if len(parts) != 4:
            continue
        address, password, client_id, refresh_token = parts
        email = address.lower()
        if "@" not in email or len(refresh_token) < 20 or not client_id:
            continue
        if email in seen:
            continue
        seen.add(email)
        accounts.append(
            {
                "email": email,
                "password": password,
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        )
    return accounts


def outlook_store_path(config: dict[str, Any]) -> Path:
    raw = str(config.get("outlook_store") or config.get("outlook_db") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "outlook-accounts.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS outlook_accounts (
            email TEXT PRIMARY KEY,
            password TEXT,
            client_id TEXT,
            refresh_token TEXT,
            status TEXT NOT NULL DEFAULT 'available',
            fail_reason TEXT,
            imported_at REAL,
            claimed_at REAL,
            finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_outlook_status ON outlook_accounts(status);
        """
    )
    connection.commit()
    return connection


def import_outlook_accounts(path: Path, text: str) -> dict[str, int]:
    rows = parse_outlook_lines(text)
    now = time.time()
    inserted = updated = skipped = 0
    with _STORE_LOCK:
        connection = _connect(path)
        try:
            for row in rows:
                current = connection.execute(
                    "SELECT refresh_token FROM outlook_accounts WHERE email=?",
                    (row["email"],),
                ).fetchone()
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO outlook_accounts(
                            email, password, client_id, refresh_token, status, imported_at
                        ) VALUES (?, ?, ?, ?, 'available', ?)
                        """,
                        (row["email"], row["password"], row["client_id"], row["refresh_token"], now),
                    )
                    inserted += 1
                    continue
                if str(current["refresh_token"] or "") == row["refresh_token"]:
                    skipped += 1
                    continue
                connection.execute(
                    """
                    UPDATE outlook_accounts
                    SET password=?, client_id=?, refresh_token=?, status='available',
                        fail_reason='', claimed_at=NULL, finished_at=NULL
                    WHERE email=?
                    """,
                    (row["password"], row["client_id"], row["refresh_token"], row["email"]),
                )
                updated += 1
            connection.commit()
        finally:
            connection.close()
    return {"imported": inserted, "updated": updated, "skipped": skipped, "total": len(rows)}


def outlook_pool_stats(path: Path) -> dict[str, int]:
    with _STORE_LOCK:
        connection = _connect(path)
        try:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM outlook_accounts GROUP BY status"
            ).fetchall()
        finally:
            connection.close()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    return {
        "available": counts.get("available", 0),
        "in_use": counts.get("in_use", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "total": sum(counts.values()),
    }


def claim_outlook_account(path: Path) -> dict[str, str]:
    now = time.time()
    with _STORE_LOCK:
        connection = _connect(path)
        try:
            row = connection.execute(
                """
                SELECT email, password, client_id, refresh_token
                FROM outlook_accounts
                WHERE status='available'
                ORDER BY imported_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise RuntimeError("no available Outlook mailbox remains in the imported pool")
            connection.execute(
                "UPDATE outlook_accounts SET status='in_use', claimed_at=?, fail_reason='' WHERE email=?",
                (now, row["email"]),
            )
            connection.commit()
            return {
                "email": str(row["email"]),
                "password": str(row["password"] or ""),
                "client_id": str(row["client_id"] or ""),
                "refresh_token": str(row["refresh_token"] or ""),
            }
        finally:
            connection.close()


def finish_outlook_account(path: Path, email: str, *, status: str, reason: str = "") -> None:
    if status not in {"available", "done", "failed"}:
        raise ValueError("invalid Outlook mailbox status")
    with _STORE_LOCK:
        connection = _connect(path)
        try:
            connection.execute(
                """
                UPDATE outlook_accounts
                SET status=?, fail_reason=?, finished_at=?
                WHERE email=?
                """,
                (status, reason[:400], time.time(), email.lower()),
            )
            connection.commit()
        finally:
            connection.close()


def refresh_outlook_access_token(refresh_token: str, client_id: str) -> dict[str, str]:
    import requests

    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "scope": IMAP_SCOPE,
            },
            timeout=20,
        )
        payload = response.json() if response.content else {}
    except Exception as exc:
        raise FatalOutlookMailError(f"outlook token refresh failed: {exc}") from exc
    if response.status_code >= 400:
        detail = payload.get("error_description") or payload.get("error") or response.text[:240]
        raise FatalOutlookMailError(f"outlook token refresh HTTP {response.status_code}: {detail}")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise FatalOutlookMailError("outlook token refresh returned no access_token")
    return {
        "access_token": token,
        "refresh_token": str(payload.get("refresh_token") or refresh_token),
    }


def _decode_header(value: str) -> str:
    parts: list[str] = []
    for chunk, encoding in decode_header(value or ""):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return " ".join(parts).strip()


def _message_bodies(message: email_lib.message.Message) -> tuple[str, tuple[str, ...]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        content_type = str(part.get_content_type() or "").lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        body = payload.decode(charset, errors="replace")
        if content_type == "text/html":
            html_parts.append(body)
        else:
            text_parts.append(body)
    return "\n".join(text_parts), tuple(html_parts)


def _folder_names(client: imaplib.IMAP4) -> list[str]:
    names: list[str] = []
    try:
        status, listing = client.list()
    except Exception:
        return list(DEFAULT_FOLDERS)
    if status != "OK":
        return list(DEFAULT_FOLDERS)
    discovered: dict[str, str] = {}
    for raw in listing or []:
        if not raw:
            continue
        line = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
        match = re_search_quoted(line)
        if not match:
            continue
        discovered[match.lower()] = match
    for candidate in DEFAULT_FOLDERS:
        real = discovered.get(candidate.lower())
        if real and real not in names:
            names.append(real)
    for key, value in discovered.items():
        if any(marker in key for marker in ("junk", "spam", "bulk")) and value not in names:
            names.append(value)
    if "INBOX" not in names:
        names.insert(0, "INBOX")
    return names or list(DEFAULT_FOLDERS)


def re_search_quoted(line: str) -> str:
    match = re.search(r'"([^"]+)"\s*$', line) or re.search(r"\s(\S+)\s*$", line)
    return match.group(1).strip('"') if match else ""


def _message_timestamp(message: email_lib.message.Message) -> float:
    value = message.get("Date") or ""
    try:
        return parsedate_to_datetime(value).timestamp()
    except Exception:
        return 0.0


class OutlookLinkReceiver:
    def __init__(self, account: dict[str, str], config: dict[str, Any]) -> None:
        self.email = str(account["email"]).strip().lower()
        self.password = str(account.get("password") or "")
        self.client_id = str(account["client_id"]).strip()
        self.refresh_token = str(account["refresh_token"]).strip()
        self.config = config
        self.store = outlook_store_path(config)
        self._consumed_codes: set[str] = set()
        self._access_token = ""
        self._token_at = 0.0

    def mark_done(self) -> None:
        finish_outlook_account(self.store, self.email, status="done")

    def mark_failed(self, reason: str) -> None:
        finish_outlook_account(self.store, self.email, status="failed", reason=reason)

    def _token(self) -> str:
        if self._access_token and time.time() - self._token_at < 3000:
            return self._access_token
        payload = refresh_outlook_access_token(self.refresh_token, self.client_id)
        self._access_token = payload["access_token"]
        self._token_at = time.time()
        if payload.get("refresh_token"):
            self.refresh_token = payload["refresh_token"]
        return self._access_token

    def _open(self) -> imaplib.IMAP4:
        client = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        auth = f"user={self.email}\x01auth=Bearer {self._token()}\x01\x01"
        status, _ = client.authenticate("XOAUTH2", lambda _challenge: auth.encode())
        if status != "OK":
            raise FatalOutlookMailError(f"outlook IMAP XOAUTH2 failed for {self.email}")
        return client

    def wait_for_verification_link(
        self,
        timeout: float = 180,
        emit: Callable[[str], None] | None = None,
        allowed_hosts: Iterable[str] = (),
    ) -> str:
        from .mailbox_link import verification_action_code

        hosts = tuple(allowed_hosts)
        deadline = time.time() + timeout
        started = time.time() - 15
        poll = max(2.0, min(float(self.config.get("mail_poll_interval") or 4), 8.0))
        last_report = 0.0
        while time.time() < deadline:
            remaining = int(max(0, deadline - time.time()))
            if emit and time.time() - last_report >= 15:
                emit(f"[phase:wait_email_link] outlook polling remaining={remaining}s email={self.email}")
                last_report = time.time()
            try:
                client = self._open()
            except FatalOutlookMailError:
                raise
            except Exception:
                time.sleep(poll)
                continue
            try:
                for folder in _folder_names(client):
                    try:
                        argument = f'"{folder}"' if " " in folder else folder
                        status, _ = client.select(argument, readonly=True)
                        if status != "OK":
                            continue
                        status, data = client.search(None, "ALL")
                    except Exception:
                        continue
                    ids = data[0].split() if status == "OK" and data and data[0] else []
                    for message_id in reversed(ids[-12:]):
                        try:
                            status, raw = client.fetch(message_id, "(BODY.PEEK[])")
                            if status != "OK" or not raw or not raw[0] or not raw[0][1]:
                                continue
                            message = email_lib.message_from_bytes(raw[0][1])
                        except Exception:
                            continue
                        stamped = _message_timestamp(message)
                        if stamped and stamped < started:
                            continue
                        subject = _decode_header(str(message.get("Subject") or ""))
                        text, html = _message_bodies(message)
                        content = merge_mail_content(
                            MailContent(str(message_id), subject, text, html),
                            mail_content_from_item({"id": str(message_id), "subject": subject, "text": text, "html": list(html)}),
                        )
                        link = extract_verification_link(content, hosts)
                        code = verification_action_code(link) if link else ""
                        if link and code not in self._consumed_codes:
                            self._consumed_codes.add(code)
                            return link
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
            time.sleep(poll)
        raise RuntimeError("timeout waiting for ElevenLabs verification link (Outlook IMAP)")


def create_outlook_mailbox(
    config: dict[str, Any],
    emit: Callable[[str], None] | None = None,
) -> tuple[str, OutlookLinkReceiver]:
    path = outlook_store_path(config)
    account = claim_outlook_account(path)
    if emit:
        emit(f"[phase:create_mailbox] outlook claimed address={account['email']}")
    return account["email"], OutlookLinkReceiver(account, config)
