import os
import sys
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

# Add project root to sys.path to allow imports from other apps
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Correctly import internal engines
from ai_engine import autogenerate_response
from messaging_engine import send_message
from apps.comms_layer.payment_gateway import initiate_payment

app = Flask(__name__)


@app.route("/ussd", methods=["POST"])
def ussd():
    # Read the variables sent via POST from our API
    session_id = request.values.get("sessionId", None)
    service_code = request.values.get("serviceCode", None)
    phone_number = request.values.get("phoneNumber", None)
    text = request.values.get("text", "")

    user_response = text.split("*") if text else []
    level = len(user_response)

    response = ""

    # ======================
    # MAIN MENU (Level 0)
    # ======================
    if text == "":
        response = "CON Welcome to Care Nest\n"
        response += "Decentralizing Domestic Work\n"
        response += "1. Employer\n"
        response += "2. Worker\n"
        response += "3. Check Contract\n"
        response += "4. AI Help Assistant\n"

    # ======================
    # EMPLOYER FLOW (1)
    # ======================
    elif user_response[0] == "1":
        # Level 1: Employer Main Menu
        if level == 1:
            response = "CON Employer Menu\n"
            response += "1. Post a New Job\n"
            response += "2. View My Applicants\n"
            response += "3. Confirm Completion\n"
            response += "4. My Wallet\n"
            response += "0. Back"

        # Level 2: Employer Sub-Menus
        elif level == 2:
            if user_response[1] == "1":  # Post Job
                response = "CON Select Job Category:\n"
                response += "1. Nanny\n2. Housekeeper\n3. Caregiver\n4. Chef"
            elif user_response[1] == "2":  # View Applicants
                response = "CON Applicants for Nanny job:\n"
                response += "1. Mary W. (4.8*)\n2. Jane D. (4.5*)\n"
                response += "Select to hire"
            elif user_response[1] == "3":  # Confirm Completion
                response = "CON Select Active Contract:\n"
                response += "1. Nanny (Jane) - $120\n2. Housekeeper (Rose) - $90"
            elif user_response[1] == "4":  # Wallet
                response = "CON Your Balance: $450\n1. Withdraw\n2. Deposit\n0. Back"
            elif user_response[1] == "0":  # Back
                return ussd_redirect("")

        # Level 3+ : Multi-step flows
        elif level >= 3:
            # Post Job Flow
            if user_response[1] == "1":
                if level == 3:
                    category = (
                        ["", "Nanny", "Housekeeper", "Caregiver", "Chef"][
                            int(user_response[2])
                        ]
                        if user_response[2].isdigit()
                        else "Other"
                    )
                    response = (
                        f"CON Posting for {category}\nEnter Location (e.g. Kilimani):"
                    )
                elif level == 4:
                    response = "CON Select Duration:\n1. 1 Day\n2. 1 Week\n3. 1 Month"
                elif level == 5:
                    # Use Gemini to generate a fair pay suggestion based on duration
                    duration_map = {"1": "1 day", "2": "1 week", "3": "1 month"}
                    dur_text = duration_map.get(user_response[4], "some time")
                    prompt = f"What is a fair daily rate for a domestic worker in {user_response[3]} for {dur_text}? Give a single number in USD."
                    fair_pay = autogenerate_response(prompt).strip()
                    # Fallback if AI fails
                    if not fair_pay.isdigit():
                        fair_pay = "120"
                    response = f"CON Estimated Fair Pay: ${fair_pay}\n"
                    response += "1. Confirm & Pay Fee\n2. Cancel"
                elif level == 6 and user_response[5] == "1":
                    # Initiate Payment for the job posting
                    initiate_payment(phone_number)
                    send_message(
                        phone_number,
                        "Your job post is live! You will be notified of applicants.",
                    )
                    response = "END Payment Initiated. Check your phone for STK Push. Your job is being published."

            # View Applicant Details
            elif user_response[1] == "2":
                name = "Mary W." if user_response[2] == "1" else "Jane D."
                response = f"CON Hiring {name}?\n1. Yes, Hire & Escrow\n2. View Profile\n3. Back"

            # Confirm Completion Flow
            elif user_response[1] == "3":
                name = "Jane" if user_response[2] == "1" else "Rose"
                response = f"CON Confirm {name} completed the job?\n"
                response += "1. Yes, release payment\n2. No, raise dispute"
                if level == 4 and user_response[3] == "1":
                    send_message(
                        phone_number, f"Payment released to {name}. Thank you!"
                    )
                    response = "END Payment released successfully. Thank you for using Care Nest!"

    # ======================
    # WORKER FLOW (2)
    # ======================
    elif user_response[0] == "2":
        if level == 1:
            response = "CON Worker Menu\n"
            response += "1. Jobs Near Me\n"
            response += "2. My Active Jobs\n"
            response += "3. My Wallet\n"
            response += "0. Back"

        elif level == 2:
            if user_response[1] == "1":
                response = "CON Available Jobs:\n"
                response += (
                    "1. Nanny - Kilimani - $120\n2. Housekeeper - Westlands - $90"
                )
            elif user_response[1] == "2":
                response = "CON Active Jobs:\n1. Nanny - 2 days left\n0. Back"
            elif user_response[1] == "3":
                response = "CON Your Balance: $85\n1. Withdraw to Mpesa\n0. Back"

        elif level == 3:
            if user_response[1] == "1":  # Applying for a job
                job = "Nanny" if user_response[2] == "1" else "Housekeeper"
                response = f"CON Apply for {job}?\n1. Confirm Application\n0. Back"
                if level == 4 and user_response[3] == "1":
                    send_message(phone_number, f"Application for {job} sent!")
                    response = (
                        "END Application submitted. The employer will notify you soon."
                    )

            elif user_response[1] == "2":  # Managing active job
                response = "CON Mark Nanny job as complete?\n1. Yes\n2. Support"
                if level == 4 and user_response[3] == "1":
                    send_message(
                        phone_number,
                        "Job marked as complete. Awaiting employer approval.",
                    )
                    response = "END Status updated. Employer has been notified."

    # ======================
    # CHECK CONTRACT (3)
    # ======================
    elif user_response[0] == "3":
        if level == 1:
            response = "CON Enter Contract ID:"
        else:
            contract_id = user_response[1]
            response = f"END Contract {contract_id}:\n"
            response += "Status: ACTIVE\n"
            response += "Escrow: DISBURSED\n"
            response += "Worker: Verified"

    # ======================
    # AI HELP ASSISTANT (4)
    # ======================
    elif user_response[0] == "4":
        if level == 1:
            response = "CON Ask Care Nest Assistant anything:"
        else:
            user_query = " ".join(user_response[1:])
            ai_answer = autogenerate_response(
                f"For a domestic worker service called CareNest: {user_query}"
            )
            # Truncate for USSD (max 160 chars usually)
            response = f"END {ai_answer[:150]}..."

    # ======================
    # FALLBACK
    # ======================
    else:
        response = "END Invalid option. Please try again."

    return response


def ussd_redirect(new_text):
    """Helper for navigating back"""
    # In a real system, you might need to handle session logic here
    # but for simplicity we return the main menu display
    return ""


if __name__ == "__main__":
    app.run(debug=True, port=8002)
