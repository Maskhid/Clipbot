"""Encrypted token vault (state/vault.enc).

Everything secret (app client IDs/secrets and every account's tokens) lives in one
Fernet-encrypted JSON file committed to the repo. The only key is the VAULT_KEY GitHub
secret, so the file is useless to anyone who sees the repo without that key.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import STATE_DIR

VAULT_PATH = STATE_DIR / "vault.enc"


class VaultError(Exception):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


class Vault:
    def __init__(self, key: str | None = None, path: Path | None = None):
        key = key or os.environ.get("VAULT_KEY")
        if not key:
            raise VaultError(
                "VAULT_KEY is not set. Run `python -m clipbot vault init` once, then set the "
                "printed key as the VAULT_KEY environment variable / GitHub secret."
            )
        try:
            self._f = Fernet(key.strip().encode())
        except Exception as e:  # noqa: BLE001
            raise VaultError("VAULT_KEY is not a valid key (it should be 44 characters)") from e
        self.path = Path(path or VAULT_PATH)
        self.data: dict = {"apps": {}, "accounts": {}}
        self.dirty = False
        if self.path.exists():
            try:
                self.data = json.loads(self._f.decrypt(self.path.read_bytes()))
            except InvalidToken as e:
                raise VaultError("VAULT_KEY does not match state/vault.enc") from e
        self.data.setdefault("apps", {})
        self.data.setdefault("accounts", {})

    # apps ---------------------------------------------------------------
    def app(self, platform: str) -> dict:
        a = self.data["apps"].get(platform)
        if not a:
            raise VaultError(
                f"No {platform} app credentials in the vault. Run `python -m clipbot auth {platform}-app ...` (see SETUP.md)."
            )
        return a

    def set_app(self, platform: str, creds: dict):
        self.data["apps"][platform] = creds
        self.dirty = True

    # accounts -----------------------------------------------------------
    def account(self, account_id: str) -> dict:
        a = self.data["accounts"].get(account_id)
        if not a:
            raise VaultError(f"Account '{account_id}' is not connected. Run `python -m clipbot auth ...` for it (see SETUP.md).")
        return copy.deepcopy(a)  # callers change their copy, then call update_account()

    def has_account(self, account_id: str) -> bool:
        return account_id in self.data["accounts"]

    def set_account(self, account_id: str, creds: dict):
        self.data["accounts"][account_id] = creds
        self.dirty = True

    def update_account(self, account_id: str, creds: dict):
        if self.data["accounts"].get(account_id) != creds:
            self.data["accounts"][account_id] = copy.deepcopy(creds)
            self.dirty = True

    def save(self, force: bool = False):
        if not (self.dirty or force):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._f.encrypt(json.dumps(self.data, sort_keys=True).encode()))
        tmp.replace(self.path)
        self.dirty = False
