from django.urls import path

from . import views

urlpatterns = [
    # Employer agent panel & command center
    path("agent/", views.employer_agent_panel, name="agent-panel"),
    path("agent/ask/", views.ask_agent, name="agent-ask"),
    path("agent/prepare/", views.prepare_engagement, name="agent-prepare"),
    path("agent/engagements/<int:pk>/", views.command_center, name="agent-command-center"),
    path("agent/engagements/<int:pk>/tick/", views.tick_engagement, name="agent-tick"),
    path("agent/approvals/<int:pk>/approve/", views.approve_action, name="agent-approve"),
    path("agent/approvals/<int:pk>/reject/", views.reject_action, name="agent-reject"),
    path("agent/approvals/<int:pk>/submit-signed/", views.submit_signed_approval, name="agent-submit-signed"),
    path("agent/ingest/", views.ingest_now, name="agent-ingest"),
    # Worker
    path("agent/my-engagements/", views.worker_engagements, name="worker-engagements"),
    path("agent/my-engagements/<int:pk>/action/", views.worker_action, name="worker-action"),
    path("agent/my-engagements/<int:pk>/submit-signed/", views.worker_submit_signed, name="worker-submit-signed"),
    path("passport/", views.work_passport, name="work-passport"),
    path("passport/verify/<uuid:credential_id>/", views.credential_verify, name="credential-verify"),
    # Wallet
    path("wallet/connect/", views.wallet_connect, name="wallet-connect"),
    path("wallet/disconnect/", views.wallet_disconnect, name="wallet-disconnect"),
    # JSON helpers
    path("api/agent/tx/<str:tx_hash>/", views.tx_status, name="agent-tx-status"),
    path("api/agent/health/", views.chain_health, name="agent-chain-health"),
]
