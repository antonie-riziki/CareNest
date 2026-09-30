from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.views.decorators.http import require_POST

from apps.contracts.models import Contract
from apps.agentic_core import memory as memory_mod
from apps.agentic_core.state import explain
from apps.ussd_app.services import handle_ussd_menu


@csrf_exempt
@require_POST
def ussd_callback(request):
    session_id = request.POST.get("sessionId", "")
    phone_number = request.POST.get("phoneNumber", "")
    text = request.POST.get("text", "") or ""
    parts = [p for p in text.split("*") if p != ""] if text else []

    # Lightweight WorkOS agent: 5 / 5*<id> / 5*<id>*<question>
    if parts and parts[0] == "5":
        response = _agent_ussd(parts)
        return HttpResponse(response, content_type="text/plain")

    # Real contract lookup: 3*<id>
    if len(parts) == 2 and parts[0] == "3" and parts[1].isdigit():
        contract = Contract.objects.filter(pk=int(parts[1])).select_related("job").first()
        if not contract:
            contract = Contract.objects.filter(engagement_id=int(parts[1])).select_related("job").first()
        if not contract:
            return HttpResponse("END No engagement found for that number.", content_type="text/plain")
        exp = explain(contract.chain_status)
        source = "DEMO" if contract.is_demo else "TESTNET"
        body = (
            f"END CareNest #{contract.pk}\n"
            f"{contract.job.title}\n"
            f"Status: {exp.headline}\n"
            f"{exp.detail}\n"
            f"Pay: {contract.currency} {contract.amount}\n"
            f"Source: {source}"
        )
        return HttpResponse(body, content_type="text/plain")

    return handle_ussd_menu(text, phone_number, session_id)


def _agent_ussd(parts: list[str]) -> str:
    if len(parts) == 1:
        return "CON WorkOS Agent\nEnter engagement number:\n(or 0 to exit)"
    if parts[1] == "0":
        return "END Thank you for using Care Nest."
    if not parts[1].isdigit():
        return "END Enter a numeric engagement id."
    contract = Contract.objects.filter(pk=int(parts[1])).select_related("job", "worker").first()
    if not contract:
        return "END Engagement not found."
    mem = memory_mod.load_for_engagement(contract)
    exp = explain(contract.chain_status)
    worker = contract.worker.get_full_name() or contract.worker.username
    summary = mem.summary or exp.detail
    if len(parts) == 2:
        return (
            f"CON {worker}'s {contract.job.title}\n"
            f"{exp.headline}\n"
            f"{summary[:120]}\n"
            f"1. Next step\n0. Exit"
        )
    if parts[-1] == "1":
        return f"END Next: {exp.next_step_employer}\nWorker: {exp.next_step_worker}"
    return "END " + (summary[:150] or exp.detail)
