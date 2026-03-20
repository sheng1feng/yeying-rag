from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import json
import secrets

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import WarehouseAppUcanCredential, WarehouseCredential, WarehouseUcanBootstrap, WarehouseUcanCredential
from knowledge.services.warehouse_scope import extract_app_id_from_path
from knowledge.utils.time import utc_now


@dataclass
class WarehouseTokenBundle:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class WarehouseSessionService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.fernet = Fernet(self.settings.token_encryption_secret.encode("utf-8"))

    @staticmethod
    def _to_utc_datetime(epoch_ms: int) -> datetime:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).replace(tzinfo=None)

    def create_challenge(self, wallet_address: str) -> dict:
        response = httpx.post(
            f"{self.settings.warehouse_base_url}/api/v1/public/auth/challenge",
            json={"address": wallet_address},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        return {
            "wallet_address": data["address"],
            "challenge": data["challenge"],
            "nonce": data["nonce"],
            "issued_at": data["issuedAt"],
            "expires_at": data["expiresAt"],
        }

    def create_ucan_bootstrap(self, db: Session, wallet_address: str) -> WarehouseUcanBootstrap:
        return self._create_ucan_bootstrap(
            db,
            wallet_address,
            self.settings.warehouse_ucan_resource,
            self.settings.warehouse_ucan_action,
        )

    def create_app_ucan_bootstrap(self, db: Session, wallet_address: str, app_id: str, action: str = "read,write") -> WarehouseUcanBootstrap:
        if not app_id or "/" in app_id:
            raise ValueError("invalid app id")
        return self._create_ucan_bootstrap(db, wallet_address, f"app:{app_id}", action or "read,write")

    def _create_ucan_bootstrap(self, db: Session, wallet_address: str, resource: str, action: str) -> WarehouseUcanBootstrap:
        wallet_lower = wallet_address.lower()
        nonce = secrets.token_hex(32)
        issued_at = utc_now()
        root_expires_at = issued_at + timedelta(hours=self.settings.warehouse_ucan_root_ttl_hours)
        expires_at = issued_at + timedelta(seconds=self.settings.warehouse_ucan_bootstrap_ttl_seconds)
        cap = [{"resource": resource, "action": action}]
        ucan_statement = {
            "aud": self.settings.warehouse_ucan_audience,
            "cap": cap,
            "exp": int(root_expires_at.timestamp() * 1000),
        }
        message = (
            f"{self.settings.warehouse_ucan_siwe_domain} wants you to sign in with your Ethereum account:\n"
            f"{wallet_address}\n\n"
            f"URI: {self.settings.warehouse_ucan_siwe_uri}\n"
            "Version: 1\n"
            f"Chain ID: {self.settings.warehouse_ucan_chain_id}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {issued_at.replace(tzinfo=UTC).isoformat().replace('+00:00', 'Z')}\n"
            f"Expiration Time: {root_expires_at.replace(tzinfo=UTC).isoformat().replace('+00:00', 'Z')}\n"
            f"UCAN-AUTH: {json.dumps(ucan_statement, separators=(',', ':'))}"
        )
        bootstrap = WarehouseUcanBootstrap(
            owner_wallet_address=wallet_lower,
            nonce=nonce,
            message=message,
            audience=self.settings.warehouse_ucan_audience,
            cap_json=cap,
            root_expires_at=root_expires_at,
            expires_at=expires_at,
        )
        db.add(bootstrap)
        db.commit()
        db.refresh(bootstrap)
        return bootstrap

    def verify_ucan_and_store(self, db: Session, wallet_address: str, nonce: str, signature: str) -> WarehouseUcanCredential:
        return self._verify_ucan_and_store(db, wallet_address, nonce, signature, None)

    def verify_app_ucan_and_store(self, db: Session, wallet_address: str, app_id: str, nonce: str, signature: str) -> WarehouseAppUcanCredential:
        if not app_id or "/" in app_id:
            raise ValueError("invalid app id")
        return self._verify_ucan_and_store(db, wallet_address, nonce, signature, app_id)

    def _verify_ucan_and_store(self, db: Session, wallet_address: str, nonce: str, signature: str, app_id: str | None):
        wallet_lower = wallet_address.lower()
        bootstrap = (
            db.query(WarehouseUcanBootstrap)
            .filter(WarehouseUcanBootstrap.owner_wallet_address == wallet_lower)
            .filter(WarehouseUcanBootstrap.nonce == nonce)
            .filter(WarehouseUcanBootstrap.consumed.is_(False))
            .order_by(WarehouseUcanBootstrap.created_at.desc())
            .first()
        )
        if bootstrap is None:
            raise ValueError("warehouse ucan bootstrap not found")
        if bootstrap.expires_at < utc_now():
            raise ValueError("warehouse ucan bootstrap expired")

        recovered = Account.recover_message(encode_defunct(text=bootstrap.message), signature=signature)
        if recovered.lower() != wallet_lower:
            raise ValueError("invalid warehouse ucan signature")

        session_private_key = Ed25519PrivateKey.generate()
        session_private_raw = session_private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        session_public_raw = session_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        session_did = self._did_key_from_public_key(session_public_raw)
        root_proof = {
            "type": "siwe",
            "iss": f"did:pkh:eth:{wallet_lower}",
            "siwe": {
                "message": bootstrap.message,
                "signature": signature,
            },
        }

        encrypted_key = self.fernet.encrypt(session_private_raw).decode("utf-8")
        if app_id is None:
            credential = db.get(WarehouseUcanCredential, wallet_lower)
            if credential is None:
                credential = WarehouseUcanCredential(
                    owner_wallet_address=wallet_lower,
                    encrypted_session_private_key="",
                    session_did=session_did,
                    audience=bootstrap.audience,
                    cap_json=bootstrap.cap_json,
                    root_proof_json=root_proof,
                    root_expires_at=bootstrap.root_expires_at,
                )
                db.add(credential)
        else:
            credential = db.get(WarehouseAppUcanCredential, {"owner_wallet_address": wallet_lower, "app_id": app_id})
            if credential is None:
                credential = WarehouseAppUcanCredential(
                    owner_wallet_address=wallet_lower,
                    app_id=app_id,
                    encrypted_session_private_key="",
                    session_did=session_did,
                    audience=bootstrap.audience,
                    cap_json=bootstrap.cap_json,
                    root_proof_json=root_proof,
                    root_expires_at=bootstrap.root_expires_at,
                )
                db.add(credential)

        credential.encrypted_session_private_key = encrypted_key
        credential.session_did = session_did
        credential.audience = bootstrap.audience
        credential.cap_json = bootstrap.cap_json
        credential.root_proof_json = root_proof
        credential.root_expires_at = bootstrap.root_expires_at
        bootstrap.consumed = True
        db.commit()
        db.refresh(credential)
        return credential

    def verify_and_store(self, db: Session, wallet_address: str, signature: str) -> WarehouseCredential:
        response = httpx.post(
            f"{self.settings.warehouse_base_url}/api/v1/public/auth/verify",
            json={"address": wallet_address, "signature": signature},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json().get("data", {})
        refresh_token = response.cookies.get("refresh_token")
        if not payload.get("token") or not refresh_token:
            raise ValueError("warehouse verify response missing token or refresh cookie")

        bundle = WarehouseTokenBundle(
            access_token=payload["token"],
            refresh_token=refresh_token,
            access_expires_at=self._to_utc_datetime(payload["expiresAt"]),
            refresh_expires_at=self._to_utc_datetime(payload["refreshExpiresAt"]),
        )
        return self._save_bundle(db, wallet_address.lower(), bundle)

    def _save_bundle(self, db: Session, wallet_address: str, bundle: WarehouseTokenBundle) -> WarehouseCredential:
        credential = db.get(WarehouseCredential, wallet_address)
        if credential is None:
            credential = WarehouseCredential(
                owner_wallet_address=wallet_address,
                encrypted_access_token="",
                encrypted_refresh_token="",
                access_expires_at=bundle.access_expires_at,
                refresh_expires_at=bundle.refresh_expires_at,
                warehouse_base_url=self.settings.warehouse_base_url,
            )
            db.add(credential)
        credential.encrypted_access_token = self.fernet.encrypt(bundle.access_token.encode("utf-8")).decode("utf-8")
        credential.encrypted_refresh_token = self.fernet.encrypt(bundle.refresh_token.encode("utf-8")).decode("utf-8")
        credential.access_expires_at = bundle.access_expires_at
        credential.refresh_expires_at = bundle.refresh_expires_at
        credential.warehouse_base_url = self.settings.warehouse_base_url
        db.commit()
        db.refresh(credential)
        return credential

    def get_binding(self, db: Session, wallet_address: str) -> WarehouseCredential | None:
        return db.get(WarehouseCredential, wallet_address.lower())

    def get_ucan_binding(self, db: Session, wallet_address: str) -> WarehouseUcanCredential | None:
        return db.get(WarehouseUcanCredential, wallet_address.lower())

    def get_app_ucan_binding(self, db: Session, wallet_address: str, app_id: str) -> WarehouseAppUcanCredential | None:
        return db.get(WarehouseAppUcanCredential, {"owner_wallet_address": wallet_address.lower(), "app_id": app_id})

    def list_app_ucan_bindings(self, db: Session, wallet_address: str) -> list[WarehouseAppUcanCredential]:
        return (
            db.query(WarehouseAppUcanCredential)
            .filter(WarehouseAppUcanCredential.owner_wallet_address == wallet_address.lower())
            .order_by(WarehouseAppUcanCredential.app_id.asc())
            .all()
        )

    def delete_binding(self, db: Session, wallet_address: str) -> None:
        credential = self.get_binding(db, wallet_address)
        ucan = self.get_ucan_binding(db, wallet_address)
        app_ucans = self.list_app_ucan_bindings(db, wallet_address)
        if credential is not None:
            db.delete(credential)
        if ucan is not None:
            db.delete(ucan)
        for app_ucan in app_ucans:
            db.delete(app_ucan)
        db.commit()

    def _decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    def get_access_token(self, db: Session, wallet_address: str) -> str:
        credential = self.get_binding(db, wallet_address)
        if credential is None:
            raise ValueError("warehouse jwt binding not found")
        now = utc_now()
        if credential.access_expires_at <= now + timedelta(minutes=3):
            credential = self.refresh_binding(db, wallet_address)
        return self._decrypt(credential.encrypted_access_token)

    def get_access_token_for_path(self, db: Session, wallet_address: str, path: str) -> str:
        normalized = "/" + str(path or "/").strip().lstrip("/")
        app_id = self._extract_app_id(normalized)
        mode = self.settings.warehouse_auth_mode.strip().lower()
        if app_id and mode in {"split", "auto", "ucan"}:
            app_ucan = self.get_app_ucan_binding(db, wallet_address, app_id)
            if app_ucan is not None:
                return self._mint_invocation_ucan(app_ucan)
            if mode == "ucan":
                raise ValueError(f"warehouse app ucan binding not found for app {app_id}")
        return self.get_access_token(db, wallet_address)

    @staticmethod
    def _extract_app_id(path: str) -> str | None:
        return extract_app_id_from_path(path)

    def refresh_binding(self, db: Session, wallet_address: str) -> WarehouseCredential:
        credential = self.get_binding(db, wallet_address)
        if credential is None:
            raise ValueError("warehouse binding not found")
        refresh_token = self._decrypt(credential.encrypted_refresh_token)
        response = httpx.post(
            f"{self.settings.warehouse_base_url}/api/v1/public/auth/refresh",
            cookies={"refresh_token": refresh_token},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json().get("data", {})
        next_refresh_token = response.cookies.get("refresh_token") or refresh_token
        bundle = WarehouseTokenBundle(
            access_token=payload["token"],
            refresh_token=next_refresh_token,
            access_expires_at=self._to_utc_datetime(payload["expiresAt"]),
            refresh_expires_at=self._to_utc_datetime(payload["refreshExpiresAt"]),
        )
        return self._save_bundle(db, wallet_address.lower(), bundle)

    def _mint_invocation_ucan(self, credential: WarehouseUcanCredential) -> str:
        if credential.root_expires_at <= utc_now() + timedelta(minutes=1):
            raise ValueError("warehouse ucan credential expired")
        session_private_raw = self.fernet.decrypt(credential.encrypted_session_private_key.encode("utf-8"))
        private_key = Ed25519PrivateKey.from_private_bytes(session_private_raw)
        invocation_exp = min(
            credential.root_expires_at,
            utc_now() + timedelta(minutes=self.settings.warehouse_ucan_invocation_ttl_minutes),
        )
        header = {"alg": "EdDSA", "typ": "UCAN"}
        payload = {
            "iss": credential.session_did,
            "aud": credential.audience,
            "cap": credential.cap_json,
            "exp": int(invocation_exp.timestamp() * 1000),
            "prf": [credential.root_proof_json],
        }
        signing_input = f"{self._b64url_json(header)}.{self._b64url_json(payload)}".encode("utf-8")
        signature = private_key.sign(signing_input)
        return f"{signing_input.decode('utf-8')}.{self._b64url(signature)}"

    @staticmethod
    def _b64url_json(value: dict) -> str:
        return WarehouseSessionService._b64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    @staticmethod
    def _did_key_from_public_key(public_key: bytes) -> str:
        prefixed = bytes([0xED, 0x01]) + public_key
        return "did:key:z" + _base58btc_encode(prefixed)


def _base58btc_encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not data:
        return ""
    number = int.from_bytes(data, "big")
    encoded = []
    while number > 0:
        number, remainder = divmod(number, 58)
        encoded.append(alphabet[remainder])
    encoded.reverse()
    leading_zeroes = 0
    for byte in data:
        if byte == 0:
            leading_zeroes += 1
        else:
            break
    return ("1" * leading_zeroes) + ("".join(encoded) or "1")
