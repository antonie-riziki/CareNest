import os
import requests
from dotenv import load_dotenv
import base64

# Load environment variables from .env file
load_dotenv()


def _basic_auth_token():
    """Build the PayHero Basic auth header from env vars (never logged)."""
    api_username = os.getenv("PAYHERO_USERNAME", "")
    api_password = os.getenv("PAYHERO_PASSWORD", "")
    if not api_username or not api_password:
        return os.getenv("PAYHERO_AUTH", "")
    encoded = base64.b64encode(f"{api_username}:{api_password}".encode()).decode()
    return f"Basic {encoded}"


def initiate_payment(phone_number):
    """
    Initiates a payment request to PayHero API using the provided phone number.
    """
    url = "https://backend.payhero.co.ke/api/v2/payments"

    headers = {
        "Content-Type": "application/json",
        "Authorization": _basic_auth_token(),
    }

    payload = {
        "amount": 10,
        "phone_number": f"254{str(phone_number)}",
        "channel_id": 3903,
        "provider": "m-pesa",
        "external_reference": "INV-009",
        "customer_name": "John Doe",
        "callback_url": "https://example.com/callback.php",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return None


def initiate_payout(phone_number, amount, *, reference=""):
    """
    Optional M-Pesa B2C/payout. Returns None when credentials are missing or
    the provider fails — callers must record FAILED, never fake COMPLETED.
    """
    token = _basic_auth_token()
    if not token:
        return None
    url = os.getenv("PAYHERO_PAYOUT_URL", "https://backend.payhero.co.ke/api/v2/payouts")
    payload = {
        "amount": float(amount or 0),
        "phone_number": str(phone_number),
        "channel": "mpesa",
        "external_reference": reference or "CARENEST-PAYOUT",
    }
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": token},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "status": data.get("status") or "PROCESSING",
            "reference": data.get("reference") or data.get("CheckoutRequestID") or reference,
        }
    except requests.exceptions.RequestException:
        return None
