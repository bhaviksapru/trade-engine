"""
API Authorizer Lambda — validates X-API-Key header and source IP for the
NinjaTrader signal endpoint. Used as a Lambda authorizer on HTTP API Gateway.
Returns IAM policy allowing or denying the request.
"""
import os
import json
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secretsm         = boto3.client("secretsmanager")
API_KEY_SECRET   = os.environ["API_KEY_SECRET_ARN"]
ALLOWED_IP       = os.environ["ALLOWED_IP"]

_cached_key: str | None = None


def handler(event, context):
    """
    HTTP API Lambda authorizer (simple response format).
    event keys: headers, requestContext.http.sourceIp
    """
    headers    = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    source_ip  = event.get("requestContext", {}).get("http", {}).get("sourceIp", "")
    provided_key = headers.get("x-api-key", "")

    # ── IP check ──────────────────────────────────────────────────────────────
    if source_ip != ALLOWED_IP:
        logger.warning(f"Rejected source IP: {source_ip} (allowed: {ALLOWED_IP})")
        return _deny()

    # ── API key check ─────────────────────────────────────────────────────────
    expected_key = _get_api_key()
    if not provided_key or provided_key != expected_key:
        logger.warning(f"Invalid or missing API key from {source_ip}")
        return _deny()

    logger.info(f"Authorised request from {source_ip}")
    return _allow(event.get("routeArn", "*"))


def _get_api_key() -> str:
    global _cached_key
    if _cached_key:
        return _cached_key
    try:
        secret = secretsm.get_secret_value(SecretId=API_KEY_SECRET)
        data   = json.loads(secret["SecretString"])
        _cached_key = data["api_key"]
        return _cached_key
    except Exception as e:
        logger.error(f"Failed to fetch API key from Secrets Manager: {e}")
        return ""


def _allow(route_arn: str) -> dict:
    return {
        "isAuthorized": True,
        "context": {"authorized": "true"},
    }


def _deny() -> dict:
    return {
        "isAuthorized": False,
    }
