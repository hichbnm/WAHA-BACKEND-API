from fastapi import HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
import os
from typing import Optional

# Define API key header scheme
api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)

async def verify_admin_token(api_key: str = Security(api_key_header)) -> str:
    """
    Verify admin API key. Raises HTTPException if invalid.
    """
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        raise HTTPException(
            status_code=500,
            detail="Admin API key not configured on server"
        )
    
    if not api_key or api_key != admin_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid admin API key"
        )
    
    return api_key

async def get_optional_admin_token(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """
    Check admin API key but don't require it. Returns None if missing or invalid.
    """
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        return None
    
    if not api_key or api_key != admin_key:
        return None
    
    return api_key
