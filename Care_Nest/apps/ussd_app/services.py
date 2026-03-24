from django.shortcuts import render

from django.http import HttpResponse
from .services import handle_ussd

# Create your views here.

import os
import sys
from flask import Flask, request

from dotenv import load_dotenv

sys.path.insert(1, "/ussd_response")

from ai_engine import autogenerate_tips_response
from messaging_engine import send_message

# app = Flask(__name__)


# @app.route("/ussd", methods=["POST"])
def ussd():
    # Read the variables sent via POST from our API
    session_id = request.values.get("sessionId", None)
    serviceCode = request.values.get("serviceCode", None)
    phone_number = request.values.get("phoneNumber", None)
    text = request.values.get("text", "")

    user_response = text.split("*")

    # ======================
    # MAIN MENU
    # ======================

    if text == "":
        response = "CON Welcome to HomeChain\n"
        response += "Decentralizing Domestic Work\n"
        response += "1. Employer\n"
        response += "2. Worker\n"
        response += "3. Check Contract\n"
        response += "4. Help\n"

    # ======================
    # EMPLOYER FLOW
    # ======================

    elif text == "1":
        response = "CON Employer Menu\n"
        response += "1. Post Job\n"
        response += "2. View Applicants\n"
        response += "3. Confirm Completion\n"
        response += "0. Back\n"

    # Post Job
    elif text == "1*1":
        response = "CON Select Job Type\n1. Nanny\n2. Housekeeper\n3. Caregiver\n"

    elif (
        len(user_response) == 3 and user_response[0] == "1" and user_response[1] == "1"
    ):
        job_type = user_response[2]
        response = "CON Enter Location:\n"

    elif (
        len(user_response) == 4 and user_response[0] == "1" and user_response[1] == "1"
    ):
        location = user_response[3]
        response = "CON Select Duration\n1. 1 Day\n2. 1 Week\n3. 1 Month\n"

    elif (
        len(user_response) == 5 and user_response[0] == "1" and user_response[1] == "1"
    ):
        response = "CON Estimated Fair Pay: $120\n"
        response += "1. Confirm & Publish\n"
        response += "2. Cancel\n"

    elif text == "1*1*1*Area*1*1":  # simplified confirmation path
        response = "END Job Posted Successfully!\nApplicants will be notified."

    # ======================
    # WORKER FLOW
    # ======================

    elif text == "2":
        response = "CON Worker Menu\n"
        response += "1. View Jobs Near Me\n"
        response += "2. My Active Jobs\n"
        response += "3. Confirm Completion\n"
        response += "0. Back\n"

    # View Jobs
    elif text == "2*1":
        response = "CON Available Jobs\n"
        response += "1. Nanny - 3km - $120\n"
        response += "2. Housekeeper - 2km - $90\n"

    elif text == "2*1*1":
        response = "CON Apply for Nanny Job?\n"
        response += "1. Yes\n"
        response += "2. No\n"

    elif text == "2*1*1*1":
        send_message(phone_number, "Application Sent Successfully.")
        response = "END Application Submitted.\nWait for Employer Selection."

    # Confirm Completion (Worker)
    elif text == "2*3":
        response = "CON Confirm Job Completed?\n"
        response += "1. Yes\n2. No\n"

    elif text == "2*3*1":
        response = "END Completion Confirmed.\nWaiting for Employer."

    # ======================
    # EMPLOYER CONFIRM COMPLETION
    # ======================

    elif text == "1*3":
        response = "CON Confirm Worker Completed Job?\n"
        response += "1. Yes\n2. No\n"

    elif text == "1*3*1":
        send_message(phone_number, "Payment Released to Worker Wallet.")
        response = "END Payment Released Successfully."

    # ======================
    # CHECK CONTRACT STATUS
    # ======================

    elif text == "3":
        response = "CON Enter Contract ID:\n"

    elif len(user_response) == 2 and user_response[0] == "3":
        contract_id = user_response[1]
        response = f"END Contract {contract_id}\n"
        response += "Status: Active\n"
        response += "Escrow: Funded\n"
        response += "Completion: Pending\n"

    # ======================
    # HELP
    # ======================

    elif text == "4":
        response = "END HomeChain protects workers & employers.\n"
        response += "Escrow payments. Verified agreements.\n"
        response += "Call Support: 0700 000000\n"

    else:
        response = "END Invalid Option. Try Again."

    return HttpResponse(response, content_type="text/plain")


# if __name__ == "__main__":
#     app.run(debug=True, port=8000)
