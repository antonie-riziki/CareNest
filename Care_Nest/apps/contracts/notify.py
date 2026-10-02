from __future__ import annotations

from apps.contracts.models import Contract, Notification


def notify(
    *,
    recipient,
    title: str,
    body: str = "",
    kind: str = Notification.Kind.CONTRACT,
    actor=None,
    engagement: Contract | None = None,
    url: str = "",
) -> Notification:
    return Notification.objects.create(
        recipient=recipient,
        actor=actor,
        engagement=engagement,
        kind=kind,
        title=title[:160],
        body=body,
        url=url[:255],
    )
