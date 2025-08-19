import logging
import random
import string
from typing import Any, Dict

from django.core.cache import cache

from apps.integration.twilio.shortcuts import (
    send_verification_code,
    verify_phone_number,
)

logger = logging.getLogger(__name__)

# Cache key prefix for verification codes
VERIFICATION_CODE_PREFIX = "phone_verification_"
# Cache key prefix for verification attempts
VERIFICATION_ATTEMPTS_PREFIX = "phone_verification_attempts_"
# Max verification attempts before lockout
MAX_VERIFICATION_ATTEMPTS = 5
# Verification code expiry in seconds (10 minutes)
VERIFICATION_CODE_EXPIRY = 10 * 60
# Lockout duration in seconds (30 minutes)
LOCKOUT_DURATION = 30 * 60


def generate_verification_code(length: int = 6) -> str:
    """
    Generate a random numeric verification code.

    Args:
        length: Length of the code (default: 6)

    Returns:
        A random numeric code
    """
    return "".join(random.choices(string.digits, k=length))


def send_phone_verification(phone_number: str) -> dict[str, Any]:
    """
    Send a verification code to a phone number.

    Args:
        phone_number: Phone number to send the code to

    Returns:
        Dict with result information
    """
    # First validate the phone number format and capability
    validation = verify_phone_number(phone_number)
    if not validation.get("success") or not validation.get("valid", False):
        logger.warning(f"Invalid phone number: {phone_number}")
        return {
            "success": False,
            "error": "Invalid phone number",
            "details": validation.get("error", "Phone number validation failed"),
        }

    # Check if the number is locked out due to too many attempts
    attempts_key = f"{VERIFICATION_ATTEMPTS_PREFIX}{phone_number}"
    attempts = cache.get(attempts_key, 0)
    if attempts >= MAX_VERIFICATION_ATTEMPTS:
        logger.warning(
            f"Phone number locked out due to too many attempts: {phone_number}"
        )
        # For testing with LocMemCache, we can't use ttl method
        try:
            locked_until = cache.ttl(attempts_key)
        except AttributeError:
            # For LocMemCache, just return an estimated timeout value
            locked_until = 300  # Default timeout value

        return {
            "success": False,
            "error": "Account locked due to too many verification attempts",
            "locked_until": locked_until,
        }

    # Generate a verification code
    code = generate_verification_code()

    # Store the code in cache with expiration
    cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number}"
    cache.set(cache_key, code, VERIFICATION_CODE_EXPIRY)

    # Send the verification code
    result = send_verification_code(phone_number, code)

    if result.get("success"):
        logger.info(f"Verification code sent to {phone_number}")
        return {
            "success": True,
            "message": "Verification code sent",
            "expires_in": VERIFICATION_CODE_EXPIRY,
        }
    else:
        logger.error(
            f"Failed to send verification code to {phone_number}: {result.get('error')}"
        )
        return {
            "success": False,
            "error": "Failed to send verification code",
            "details": result.get("error", "Unknown error"),
        }


def verify_phone_code(phone_number: str, code: str) -> dict[str, Any]:
    """
    Verify a phone verification code.

    Args:
        phone_number: Phone number to verify
        code: Verification code to check

    Returns:
        Dict with verification result
    """
    # Check if the number is locked out due to too many attempts
    attempts_key = f"{VERIFICATION_ATTEMPTS_PREFIX}{phone_number}"
    attempts = cache.get(attempts_key, 0)
    if attempts >= MAX_VERIFICATION_ATTEMPTS:
        logger.warning(
            f"Phone number locked out due to too many attempts: {phone_number}"
        )
        # For testing with LocMemCache, we can't use ttl method
        try:
            locked_until = cache.ttl(attempts_key)
        except AttributeError:
            # For LocMemCache, just return an estimated timeout value
            locked_until = 300  # Default timeout value

        return {
            "success": False,
            "error": "Account locked due to too many verification attempts",
            "locked_until": locked_until,
        }

    # Get the stored code
    cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number}"
    stored_code = cache.get(cache_key)

    if not stored_code:
        logger.warning(f"No verification code found for {phone_number} or code expired")
        return {"success": False, "error": "Verification code expired or not found"}

    # Check if the code matches
    if code == stored_code:
        # Code is valid, clear the verification code and attempts
        cache.delete(cache_key)
        cache.delete(attempts_key)

        logger.info(f"Phone number verified successfully: {phone_number}")
        return {"success": True, "message": "Phone number verified successfully"}
    else:
        # Code is invalid, increment attempts
        new_attempts = attempts + 1
        cache.set(attempts_key, new_attempts, LOCKOUT_DURATION)

        remaining_attempts = MAX_VERIFICATION_ATTEMPTS - new_attempts

        logger.warning(
            f"Invalid verification code for {phone_number}. "
            f"Attempts: {new_attempts}/{MAX_VERIFICATION_ATTEMPTS}"
        )

        if remaining_attempts <= 0:
            return {
                "success": False,
                "error": "Account locked due to too many failed attempts",
                "locked_until": LOCKOUT_DURATION,
            }
        else:
            return {
                "success": False,
                "error": "Invalid verification code",
                "remaining_attempts": remaining_attempts,
            }


def reset_verification_attempts(phone_number: str) -> None:
    """
    Reset verification attempts for a phone number.

    Args:
        phone_number: Phone number to reset attempts for
    """
    attempts_key = f"{VERIFICATION_ATTEMPTS_PREFIX}{phone_number}"
    cache.delete(attempts_key)
    logger.info(f"Reset verification attempts for {phone_number}")


def is_phone_verified(phone_number: str) -> bool:
    """
    Check if a phone number has been verified recently.
    This would be used in conjunction with a user profile that stores
    verified phone numbers.

    Args:
        phone_number: Phone number to check

    Returns:
        True if verified, False otherwise
    """
    # This is a placeholder. In a real application, you would check
    # if the phone number is associated with the user's profile
    # and has been verified.
    return False
