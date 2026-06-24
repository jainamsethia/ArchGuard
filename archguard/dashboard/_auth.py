import os
import logging
from fastapi import Request, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

def check_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    token = os.environ.get("ARCHGUARD_DASHBOARD_TOKEN")
    if token:
        if not credentials or credentials.credentials != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        client_host = request.client.host if request.client else "unknown"
        if client_host not in (
            "127.0.0.1",
            "localhost",
            "::1",
            "testclient",
            "testserver",
        ):
            allow_remote = os.environ.get(
                "ARCHGUARD_DASHBOARD_ALLOW_REMOTE", ""
            ).lower() in ("1", "true")
            if not allow_remote:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Dashboard requires ARCHGUARD_DASHBOARD_TOKEN to be set for remote access",
                )
            else:
                logging.warning(
                    f"Dashboard accessed from {client_host} without token authentication! Consider setting ARCHGUARD_DASHBOARD_TOKEN."
                )
