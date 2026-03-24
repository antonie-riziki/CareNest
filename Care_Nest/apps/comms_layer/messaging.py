import africastalking
import os
from dotenv import load_dotenv

load_dotenv()

africastalking.initialize(username="EMID", api_key=os.getenv("AT_API_KEY"))

sms = africastalking.SMS


# User New Account Welcome Notification
def worker_account_activation_message(phone_number):

    recipients = [f"+{str(phone_number)}"]

    # Set your message
    message = f"Welcome to Care Nest! A safe and trusted space for domestic work. Find jobs, grow, and get paid securely"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")


# Employer New Account Welcome Notification
def employer_account_activation_message(phone_number):

    recipients = [f"{str(phone_number)}"]

    # Set your message
    message = f"Welcome to Care Nest! Access premium domestic services and manage your household with ease. Professional care, verified and secure"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")


# Daily Listing Notification
def daily_listing_notification(phone_number, message_context):

    recipients = [f"{str(phone_number)}"]

    # Set your message
    message = f"{message_context}"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")


# Job New Listing Notification
def job_new_listing_notification(phone_number, message_context):

    recipients = [f"{str(phone_number)}"]

    # Set your message
    message = f"{message_context}"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")


# Job Application Notification
def job_application_notification(phone_number, message_context):

    recipients = [f"{str(phone_number)}"]

    # Set your message
    message = f"{message_context}"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")


# Courses Notification
def courses_notification(phone_number, message_context):

    recipients = [f"{str(phone_number)}"]

    # Set your message
    message = f"{message_context}"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")


# Payment Transactions Notification
def payment_transactions_notification(phone_number, message_context):

    recipients = [f"{str(phone_number)}"]

    # Set your message
    message = f"{message_context}"

    # Set your shortCode or senderId
    sender = 20384

    try:
        response = sms.send(message, recipients, sender)

        print(response)

    except Exception as e:
        print(f"Houston, we have a problem: {e}")
