from django.shortcuts import render

from django.http import HttpResponse
from .services import handle_ussd


# Create your views here.
def ussd_callback(request):
    session_id = request.POST.get("sessionId")
    phone_number = request.POST.get("phoneNumber")
    text = request.POST.get("text")

    # response = handle_ussd()

    response = "CON Welcome to HomeChain\n"
    response += "Decentralizing Domestic Work\n"
    response += "1. Employer\n"
    response += "2. Worker\n"
    response += "3. Check Contract\n"
    response += "4. Help\n"

    return HttpResponse(response, content_type="text/plain")
