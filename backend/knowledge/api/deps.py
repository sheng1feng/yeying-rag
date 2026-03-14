from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from knowledge.db.session import get_db
from knowledge.services.auth import AuthService


bearer_scheme = HTTPBearer(auto_error=False)
auth_service = AuthService()


def get_current_wallet(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = auth_service.parse_token(credentials.credentials, expected_type="access")
        return str(payload["wallet_address"]).lower()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


DBSession = Depends(get_db)
