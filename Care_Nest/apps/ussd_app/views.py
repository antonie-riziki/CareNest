from django.shortcuts import render

from django.http import HttpResponse
from .services import handle_ussd


# Create your views here.
def ussd_callback(request):
    session_id = request.POST.get("sessionId")
    phone_number = request.POST.get("phoneNumber")
    text = request.POST.get("text")

    response = handle_ussd(session_id, phone_number, text)

    return HttpResponse(response, content_type="text/plain")
