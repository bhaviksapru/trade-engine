"""
Cognito JWT verification for FastAPI.
Validates the Bearer token issued by Cognito after Google login.
Also checks the token's email claim matches the allowed Google email.
"""
import os
import httpx
import logging
from functools import lru_cache
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

COGNITO_REGION    = os.environ["COGNITO_REGION"]
COGNITO_USER_POOL = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
ALLOWED_EMAIL     = os.environ["ALLOWED_GOOGLE_EMAIL"]

JWKS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL}/.well-known/jwks.json"

bearer = HTTPBearer()


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    return PyJWKClient(JWKS_URL)


async def verify_token(credentials: HTTPAuthorizationCredentials = Security(bearer)) -> dict:
    token = credentials.credentials
    try:
        client = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID,
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    # Enforce single-user: only the configured Google email can access
    email = payload.get("email") or payload.get("cognito:username", "")
    if email != ALLOWED_EMAIL:
        logger.warning(f"Access denied for email: {email}")
        raise HTTPException(status_code=403, detail="Access denied")

    return payload
