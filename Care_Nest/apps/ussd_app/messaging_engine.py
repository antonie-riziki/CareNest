import africastalking
import os
from dotenv import load_dotenv

load_dotenv()

_AT_API_KEY = os.getenv("AT_API_KEY")

if _AT_API_KEY:
    africastalking.initialize(username=os.getenv("AT_USERNAME", "EMID"), api_key=_AT_API_KEY)
    sms = africastalking.SMS
else:

    class _DisabledSMS:
        def send(self, message, recipients, sender=None):
            return {"status": "disabled", "reason": "AT_API_KEY unset"}

    sms = _DisabledSMS()


def send_message(phone_number, message_context):

    recipients = [f"{str(phone_number)}"]

    print(recipients)
    print(phone_number)

    # Set your message
    message = f"{message_context}"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")
