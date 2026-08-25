from __future__ import annotations

import argparse
import getpass
import sys
from typing import Any

from protocol_register import create_mailbox

from .browser_flow import dry_run_browser, run_assisted_registration, run_automated_registration
from .config import ElevenLabsConfig
from .credentials import save_credentials
from .mailbox_link import MailContent, extract_verification_link
from .passwords import generate_password
from .proxy_preflight import preflight_proxy


def phase(name: str, message: str) -> None:
    print(f"[phase:{name}] {message}", flush=True)


class ManualLinkReceiver:
    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        self.allowed_hosts = allowed_hosts

    def wait_for_verification_link(self, **_kwargs: Any) -> str:
        value = getpass.getpass("Paste the full verification URL from the mailbox (input is hidden): ").strip()
        content = MailContent("manual", "", value, ())
        link = extract_verification_link(content, self.allowed_hosts)
        if not link:
            raise ValueError("the pasted URL is not an allowed ElevenLabs verification link")
        return link


def _load_config(path: str) -> ElevenLabsConfig:
    try:
        return ElevenLabsConfig.load(path)
    except Exception as exc:
        raise SystemExit(f"configuration error: {exc}") from exc


def _preflight(config: ElevenLabsConfig) -> None:
    phase("network_preflight", "checking ElevenLabs HTTPS connectivity")
    result = preflight_proxy(
        config.proxy_url,
        target_url=config.preflight_url,
        timeout=config.proxy_timeout,
    )
    phase(
        "network_preflight",
        f"ok connection={result.proxy_label} target={result.target_host} HTTP {result.status_code}",
    )


def _prepare_account(config: ElevenLabsConfig) -> tuple[str, Any, str]:
    if config.email:
        email = config.email
        receiver: Any = ManualLinkReceiver(config.allowed_verification_hosts)
        phase("create_mailbox", "using the configured email address; verification link input will be manual")
    else:
        phase("create_mailbox", "creating one mailbox with the configured provider")
        email, receiver = create_mailbox(config.mail, emit=print)
    password = config.password or generate_password()
    print("\nAccount credentials for this single run:")
    print(f"  Email:    {email}")
    print(f"  Password: {password}")
    print("Keep these locally. The CLI does not export browser cookies or Firebase tokens.\n")
    return email, receiver, password


def _persist(config: ElevenLabsConfig, email: str, password: str, final_url: str) -> None:
    if not config.save_credentials:
        return
    path = save_credentials(
        config.credentials_file,
        email=email,
        password=password,
        extra={"final_url": final_url},
    )
    phase("done", f"credentials saved to {path}")


def _run(config: ElevenLabsConfig, *, automated: bool) -> None:
    if automated:
        config.validate_for_automated_run()
    else:
        config.validate_for_run()
    _preflight(config)
    email, receiver, password = _prepare_account(config)
    runner = run_automated_registration if automated else run_assisted_registration
    result = runner(
        config,
        email=email,
        password=password,
        receiver=receiver,
        emit=print,
    )
    _persist(config, result.email, password, result.final_url)
    phase("done", f"authenticated={result.authenticated} email={result.email} url={result.final_url}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ElevenLabs single-account registration")
    parser.add_argument("--config", required=True, help="Path to a local JSON config file")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="Check config, proxy listener, and ElevenLabs HTTPS")
    subparsers.add_parser("dry-run", help="Open the sign-up page and verify selectors without submitting")
    subparsers.add_parser("run", help="Run one fully automated registration")
    subparsers.add_parser("run-assisted", help="Run one user-assisted registration")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = _load_config(args.config)
    try:
        if args.command == "preflight":
            _preflight(config)
        elif args.command == "dry-run":
            _preflight(config)
            dry_run_browser(config, emit=print)
        elif args.command == "run-assisted":
            _run(config, automated=False)
        else:
            _run(config, automated=True)
    except KeyboardInterrupt:
        phase("stopped", "cancelled by user")
        raise SystemExit(130) from None
    except Exception as exc:
        phase("failed", str(exc)[:500])
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main(sys.argv[1:])
