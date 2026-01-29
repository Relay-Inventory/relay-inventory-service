from __future__ import annotations

from fastapi import Header, HTTPException, status


class ApiKeyAuth:
    def __init__(self, valid_keys: set[str]) -> None:
        self.valid_keys = valid_keys

    def __call__(self, x_api_key: str | None = Header(default=None)) -> None:
        if not self.valid_keys:
            return
        if not x_api_key or x_api_key not in self.valid_keys:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
