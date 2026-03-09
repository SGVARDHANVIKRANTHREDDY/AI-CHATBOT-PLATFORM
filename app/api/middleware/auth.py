from __future__ import annotations
from fastapi import Request, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN
import os

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Security(api_key_header)):
    expected = os.getenv("API_KEY")
    if not expected:
        # If no key is configured, allow for now (optional)
        return api_key
    
    if api_key == expected:
        return api_key
    else:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN, detail="Could not validate API Key"
        )
