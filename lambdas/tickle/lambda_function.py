"""
Tickle Lambda - keeps IBKR CP Gateway session alive.
Runs every 55 seconds via CloudWatch Events rule.
Also checks auth status and triggers re-auth if session expired.
"""
import os
import json
import boto3
import httpx
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sns = boto3.client("sns")
dynamodb = boto3.resource("dynamodb")
config_table = dynamodb.Table(os.environ["CONFIG_TABLE"])

CP_GATEWAY_URL = os.environ["CP_GATEWAY_URL"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
MAX_REAUTH_ATTEMPTS = 3


def handler(event, context):
    # 1. Check auth status
    try:
        status_resp = httpx.get(
            f"{CP_GATEWAY_URL}/v1/api/iserver/auth/status",
            verify=False, timeout=5
        )
        auth_data = status_resp.json()
        authenticated = auth_data.get("authenticated", False)
    except Exception as e:
        logger.error(f"CP Gateway unreachable: {e}")
        _alert(f"CP Gateway unreachable: {e}")
        _set_trading_enabled(False, reason="cp_gateway_unreachable")
        return {"status": "error", "message": str(e)}

    if authenticated:
        # 2. Send tickle to keep session alive
        try:
            tickle_resp = httpx.post(
                f"{CP_GATEWAY_URL}/v1/api/tickle",
                verify=False, timeout=5
            )
            logger.info(f"Tickle sent - session alive. Status: {tickle_resp.status_code}")
            _set_trading_enabled(True, reason="session_alive")
            return {"status": "ok", "authenticated": True}
        except Exception as e:
            logger.error(f"Tickle failed: {e}")
            return {"status": "error", "message": str(e)}
    else:
        # 3. Session expired - attempt re-authentication
        logger.warning("CP Gateway session expired - attempting reauthentication")
        for attempt in range(1, MAX_REAUTH_ATTEMPTS + 1):
            try:
                reauth_resp = httpx.post(
                    f"{CP_GATEWAY_URL}/v1/api/iserver/reauthenticate",
                    verify=False, timeout=10
                )
                if reauth_resp.status_code == 200:
                    logger.info(f"Reauthentication successful (attempt {attempt})")
                    _set_trading_enabled(True, reason="reauth_success")
                    return {"status": "reauthenticated", "attempt": attempt}
            except Exception as e:
                logger.warning(f"Reauth attempt {attempt} failed: {e}")

        # All reauth attempts failed
        logger.error("All reauthentication attempts failed - disabling trading")
        _set_trading_enabled(False, reason="reauth_failed")
        _alert("IBKR CP Gateway authentication failed after 3 attempts. Trading disabled. Manual intervention required.")
        return {"status": "auth_failed"}


def _set_trading_enabled(enabled: bool, reason: str):
    try:
        config_table.put_item(Item={
            "pk": "trading_enabled",
            "value": enabled,
            "reason": reason,
        })
    except Exception as e:
        logger.error(f"Failed to update trading_enabled: {e}")


def _alert(message: str):
    try:
        # Check if alerts are enabled
        result = config_table.get_item(Key={"pk": "notification_preferences"})
        prefs = result.get("Item", {})
        if prefs.get("enabled") and prefs.get("events", {}).get("auth_failure", True):
            sns.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="Trade Engine Alert")
    except Exception as e:
        logger.error(f"SNS alert failed: {e}")
