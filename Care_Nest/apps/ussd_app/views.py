from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods

from apps.ussd_app.services import handle_ussd_menu


@csrf_exempt
@require_http_methods(["GET", "POST"])
def ussd_callback(request):
    """Africa's Talking webhook. Same CON/END body as the Flask /ussd app."""
    if request.method == "GET":
        return HttpResponse(
            "CareNest USSD is up. POST text, phoneNumber, sessionId (Africa's Talking).",
            content_type="text/plain",
        )
    session_id = request.POST.get("sessionId", "") or request.GET.get("sessionId", "")
    phone_number = request.POST.get("phoneNumber", "") or request.GET.get("phoneNumber", "")
    text = request.POST.get("text", "") or request.GET.get("text", "") or ""
    return handle_ussd_menu(text, phone_number, session_id)
