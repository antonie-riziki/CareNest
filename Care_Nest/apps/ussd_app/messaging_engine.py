import os
import sys
from dotenv import load_dotenv

load_dotenv()

_AT_API_KEY = os.getenv("AT_API_KEY")
sms = None


def _in_tests() -> bool:
    return "test" in sys.argv or bool(os.getenv("PYTEST_CURRENT_TEST"))


if _AT_API_KEY and not _in_tests():
    try:
        import africastalking

        africastalking.initialize(username=os.getenv("AT_USERNAME", "EMID"), api_key=_AT_API_KEY)
        sms = africastalking.SMS
    except Exception:
        sms = None

if sms is None:

    class _DisabledSMS:
        def send(self, message, recipients, sender=None):
            return {"status": "disabled", "reason": "Africa's Talking is not configured"}

    sms = _DisabledSMS()


def send_message(phone_number, message_context):
    if _in_tests() or not phone_number:
        return
    recipients = [f"{str(phone_number)}"]
    message = f"{message_context}"
    sender = 20384
    try:
        sms.send(message, recipients, sender)
    except Exception as exc:
        print(f"Africa's Talking SMS skipped: {exc}")
