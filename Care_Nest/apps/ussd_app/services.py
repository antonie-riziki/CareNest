from django.http import HttpResponse

from apps.jobs.models import Application
from apps.ussd_app.db import (
    JOB_TYPES,
    DURATIONS,
    create_ussd_job,
    employer_applicants,
    employer_open_contracts,
    nearby_jobs,
    typical_pay,
    user_for_phone,
    wallet_for,
    worker_active_contracts,
)
from .messaging_engine import send_message


def _con(lines):
    return "CON " + "\n".join(lines)


def _end(lines):
    return "END " + "\n".join(lines)


def handle_ussd_menu(text, phone_number=None, session_id=None):
    return HttpResponse(ussd_text(text, phone_number, session_id), content_type="text/plain")


def _agent_ussd(parts: list[str]) -> str:
    from apps.agentic_core import memory as memory_mod
    from apps.agentic_core.state import explain
    from apps.contracts.models import Contract

    if len(parts) == 1:
        return _con(["WorkOS Agent", "Enter engagement number:", "(or 0 to exit)"])
    if parts[1] == "0":
        return _end(["Thank you for using Care Nest."])
    if not parts[1].isdigit():
        return _end(["Enter a numeric engagement id."])
    contract = Contract.objects.filter(pk=int(parts[1])).select_related("job", "worker").first()
    if not contract:
        return _end(["Engagement not found."])
    mem = memory_mod.load_for_engagement(contract)
    exp = explain(contract.chain_status)
    worker = contract.worker.get_full_name() or contract.worker.username
    summary = mem.summary or exp.detail
    if len(parts) == 2:
        return _con(
            [
                f"{worker}'s {contract.job.title}",
                exp.headline,
                summary[:120],
                "1. Next step",
                "0. Exit",
            ]
        )
    if parts[-1] == "1":
        return _end([f"Next: {exp.next_step_employer}", f"Worker: {exp.next_step_worker}"])
    return _end([summary[:150] or exp.detail])


def _wallet_menu(user, phone_number=None) -> str:
    if not user:
        return _end(["Register in the CareNest app first, then dial again."])
    wallet = wallet_for(user)
    balance = wallet.balance if wallet else 0
    address = (wallet.short_address if wallet else "") or "Not connected"
    send_message(phone_number, f"CareNest wallet balance KES {balance}.")
    return _end(
        [
            f"Wallet KES {balance}",
            f"Stellar {address}",
            "Withdraw or deposit in the CareNest app.",
        ]
    )


def ussd_text(text, phone_number=None, session_id=None) -> str:
    """Africa's Talking CON/END body, backed by the live CareNest database."""
    text = text or ""
    parts = [p for p in text.split("*") if p != ""] if text else []
    user = user_for_phone(phone_number)

    if parts and parts[0] == "5":
        return _agent_ussd(parts)

    if text == "":
        response = _con(
            [
                "Welcome to Care Nest",
                "Decentralizing Domestic Work",
                "1. Employer",
                "2. Worker",
                "3. Check Contract",
                "4. Help",
                "5. WorkOS Agent",
            ]
        )

    elif text == "1":
        response = _con(
            ["Employer Menu", "1. Post Job", "2. View Applicants", "3. Confirm Completion", "4. My Wallet", "0. Back"]
        )

    elif text == "1*4":
        response = _wallet_menu(user, phone_number)

    elif text == "1*1":
        response = _con(["Select Job Type", "1. Nanny", "2. Housekeeper", "3. Caregiver"])

    elif len(parts) == 3 and parts[0] == "1" and parts[1] == "1":
        if parts[2] not in JOB_TYPES:
            response = _end(["Invalid job type."])
        else:
            response = _con(["Enter Location:", "Use your estate or area name."])

    elif len(parts) == 4 and parts[0] == "1" and parts[1] == "1":
        response = _con(["Select Duration", "1. 1 Day", "2. 1 Week", "3. 1 Month"])

    elif len(parts) == 5 and parts[0] == "1" and parts[1] == "1":
        job_type = JOB_TYPES.get(parts[2])
        if not job_type or parts[4] not in DURATIONS:
            response = _end(["Invalid option."])
        else:
            pay = typical_pay(job_type)
            response = _con(
                [
                    f"Estimated Fair Pay: KES {pay}",
                    f"{job_type.title()} · {parts[3]} · {DURATIONS[parts[4]][0]}",
                    "1. Confirm & Publish",
                    "2. Cancel",
                ]
            )

    elif len(parts) == 6 and parts[0] == "1" and parts[1] == "1" and parts[5] == "1":
        if not user or user.role != "employer":
            response = _end(["Register as an employer in the CareNest app first, then dial again."])
        else:
            job_type = JOB_TYPES.get(parts[2])
            duration = DURATIONS.get(parts[4], ("as agreed", 0))[0]
            job = create_ussd_job(user, job_type, parts[3], duration)
            send_message(phone_number, f"Job #{job.pk} posted: {job.title}")
            response = _end([f"Job Posted Successfully!", f"{job.title}", f"KES {job.pay}", "Applicants will be notified."])

    elif len(parts) == 6 and parts[0] == "1" and parts[1] == "1":
        response = _end(["Cancelled. No job was posted."])

    elif text == "1*2":
        if not user or user.role != "employer":
            response = _end(["Register as an employer in the CareNest app first."])
        else:
            rows = list(employer_applicants(user))
            if not rows:
                response = _end(["No applicants yet on your live jobs."])
            else:
                lines = ["Applicants"]
                for idx, app in enumerate(rows, start=1):
                    name = app.worker.get_full_name() or app.worker.username
                    lines.append(f"{idx}. {name} · {app.job.title} · {app.status}")
                response = _end(lines)

    elif text == "1*3":
        if not user or user.role != "employer":
            response = _end(["Register as an employer in the CareNest app first."])
        else:
            rows = list(employer_open_contracts(user))
            if not rows:
                response = _end(["No engagements awaiting confirmation."])
            else:
                lines = ["Confirm worker completed?"]
                for idx, contract in enumerate(rows, start=1):
                    name = contract.worker.get_full_name() or contract.worker.username
                    lines.append(f"{idx}. #{contract.pk} {contract.job.title} · {name} · {contract.chain_status}")
                lines.append("Reply with the engagement number, or 0 to cancel.")
                response = _con(lines)

    elif len(parts) == 3 and parts[0] == "1" and parts[1] == "3":
        if parts[2] == "0":
            response = _end(["Cancelled."])
        elif not user or not parts[2].isdigit():
            response = _end(["Invalid option."])
        else:
            contract = next((c for i, c in enumerate(employer_open_contracts(user), start=1) if str(i) == parts[2] or str(c.pk) == parts[2]), None)
            if not contract:
                response = _end(["Engagement not found."])
            else:
                send_message(phone_number, f"Open CareNest to release payment for #{contract.pk}.")
                response = _end(
                    [
                        f"Engagement #{contract.pk} is {contract.chain_status}.",
                        "Release payment from the employer command center after both sides agree.",
                    ]
                )

    elif text == "2":
        response = _con(
            ["Worker Menu", "1. View Jobs Near Me", "2. My Active Jobs", "3. Confirm Completion", "4. My Wallet", "0. Back"]
        )

    elif text == "2*4":
        response = _wallet_menu(user, phone_number)

    elif text == "2*1":
        worker = user if user and user.role == "worker" else None
        rows = nearby_jobs(worker)
        if not rows:
            response = _end(["No live jobs posted yet."])
        else:
            lines = ["Available Jobs"]
            for idx, (job, km) in enumerate(rows, start=1):
                distance = f"{km}km" if km is not None else job.location
                lines.append(f"{idx}. {job.title} · {distance} · {job.currency} {job.pay}")
            lines.append("Reply with a number to apply.")
            response = _con(lines)

    elif len(parts) == 3 and parts[0] == "2" and parts[1] == "1":
        worker = user if user and user.role == "worker" else None
        rows = nearby_jobs(worker)
        if not parts[2].isdigit() or int(parts[2]) < 1 or int(parts[2]) > len(rows):
            response = _end(["Invalid job number."])
        else:
            job, km = rows[int(parts[2]) - 1]
            extra = f"{km} km away" if km is not None else job.location
            response = _con([f"Apply for {job.title}?", extra, f"{job.currency} {job.pay}", "1. Yes", "2. No"])

    elif len(parts) == 4 and parts[0] == "2" and parts[1] == "1" and parts[3] == "1":
        if not user or user.role != "worker":
            response = _end(["Register as a worker in the CareNest app first."])
        else:
            rows = nearby_jobs(user)
            index = int(parts[2]) - 1
            if index < 0 or index >= len(rows):
                response = _end(["Invalid job number."])
            else:
                job, _km = rows[index]
                if job.locked:
                    response = _end(["That job is already locked."])
                else:
                    Application.objects.get_or_create(worker=user, job=job, defaults={"status": Application.Status.SUBMITTED})
                    send_message(phone_number, f"Application sent for {job.title}.")
                    response = _end(["Application Submitted.", "Add your terms in the CareNest app so the employer can review them."])

    elif len(parts) == 4 and parts[0] == "2" and parts[1] == "1":
        response = _end(["No application sent."])

    elif text == "2*2":
        if not user or user.role != "worker":
            response = _end(["Register as a worker in the CareNest app first."])
        else:
            rows = list(worker_active_contracts(user))
            if not rows:
                response = _end(["You have no active engagements."])
            else:
                lines = ["My Active Jobs"]
                for contract in rows:
                    lines.append(f"#{contract.pk} {contract.job.title} · {contract.get_approval_status_display()} · {contract.chain_status}")
                response = _end(lines)

    elif text == "2*3":
        if not user or user.role != "worker":
            response = _end(["Register as a worker in the CareNest app first."])
        else:
            rows = list(worker_active_contracts(user))
            if not rows:
                response = _end(["No engagement to confirm."])
            else:
                lines = ["Confirm job completed?"]
                for idx, contract in enumerate(rows, start=1):
                    lines.append(f"{idx}. #{contract.pk} {contract.job.title} · {contract.chain_status}")
                lines.append("1. Yes for the first job  0. Cancel")
                response = _con(lines)

    elif text == "2*3*1":
        if not user or user.role != "worker":
            response = _end(["Register as a worker in the CareNest app first."])
        else:
            contract = worker_active_contracts(user).first()
            if not contract:
                response = _end(["No engagement to confirm."])
            else:
                send_message(phone_number, f"Completion noted for #{contract.pk}. Employer still confirms release.")
                response = _end(["Completion Confirmed.", f"{contract.job.title}", "Waiting for Employer."])

    elif text == "2*3*0" or text == "1*0" or text == "2*0":
        response = _con(
            [
                "Welcome to Care Nest",
                "1. Employer",
                "2. Worker",
                "3. Check Contract",
                "4. Help",
                "5. WorkOS Agent",
            ]
        )

    elif text == "3":
        response = _con(["Enter Contract ID:"])

    elif len(parts) == 2 and parts[0] == "3":
        from apps.contracts.models import Contract
        from apps.agentic_core.state import explain

        contract = None
        if parts[1].isdigit():
            contract = Contract.objects.filter(pk=int(parts[1])).select_related("job").first()
            if not contract:
                contract = Contract.objects.filter(engagement_id=int(parts[1])).select_related("job").first()
        if not contract:
            response = _end(["No engagement found for that number."])
        else:
            exp = explain(contract.chain_status)
            response = _end(
                [
                    f"Contract {contract.pk}",
                    contract.job.title,
                    f"Status: {exp.headline}",
                    exp.detail,
                    f"Pay: {contract.currency} {contract.amount}",
                    f"Source: {'DEMO' if contract.is_demo else 'TESTNET'}",
                ]
            )

    elif text == "4":
        response = _end(
            [
                "Care Nest protects workers & employers.",
                "Jobs, terms, and contracts are stored in CareNest.",
                "Pin your live location in the app to see nearby work.",
            ]
        )

    else:
        response = _end(["Invalid Option. Try Again."])

    return response
