"""API kimlik dogrulama: Bearer token, sabit zamanli karsilastirma.

`/health` disinda tum yollar korumali. Token loglanmaz.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from devbrief.config import get_settings

_bearer = HTTPBearer(auto_error=False)


async def require_token(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = get_settings().devbrief_api_token or ""
    given = (creds.credentials if creds else "")
    if not expected or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="gecersiz token", headers={"WWW-Authenticate": "Bearer"})
