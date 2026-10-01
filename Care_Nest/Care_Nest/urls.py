"""
URL configuration for Care_Nest project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from apps.core.views import *
from apps.accounts.views import *
from apps.dashboard.views import *
from apps.jobs.views import *
from apps.contracts.views import *
from apps.profiles.views import *
from apps.wallet.views import *
from apps.courses.views import *
from apps.acc_settings.views import *
from apps.ussd_app.views import ussd_callback

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", index, name="home"),
    path("offline/", pwa_offline, name="pwa-offline"),
    path("manifest.webmanifest", pwa_manifest, name="pwa-manifest"),
    path("sw.js", pwa_service_worker, name="pwa-sw"),
    path("role-selection/", role_selection, name="role-selection"),
    path("worker-signup/", worker_signup, name="worker-signup"),
    path("worker-signin/", worker_signin, name="worker-signin"),
    path("employer-signup/", employer_signup, name="employer-signup"),
    path("employer-signin/", employer_signin, name="employer-signin"),
    path("worker-profile/", worker_profile, name="worker-profile"),
    path("employer-profile/", employer_profile, name="employer-profile"),
    path("worker-dashboard/", worker_dashboard, name="worker-dashboard"),
    path("employer-dashboard/", employer_dashboard, name="employer-dashboard"),
    path("worker-jobs/", worker_jobs, name="worker-jobs"),
    path("employer-jobs/", employer_jobs, name="employer-jobs"),
    path("employer-jobs/new/", employer_job_form, name="employer-job-new"),
    path("employer-jobs/<int:pk>/edit/", employer_job_form, name="employer-job-edit"),
    path("employer-jobs/<int:pk>/save/", save_employer_job, name="employer-job-save"),
    path("employer-jobs/<int:pk>/image/remove/", remove_job_image, name="employer-job-remove-image"),
    path("jobs/<int:pk>/terms/", submit_job_terms, name="job-submit-terms"),
    path("jobs/<int:pk>/apply/", apply_to_job, name="job-apply"),
    path("worker-job-details/", worker_job_details, name="worker-job-details"),
    path("employer-job-details/", employer_job_details, name="employer-job-details"),
    path("worker-contracts/", worker_contracts, name="worker-contracts"),
    path("employer-contracts/", employer_contracts, name="employer-contracts"),
    path("employer-engagements/<int:pk>/", employer_engagement_review, name="employer-engagement-review"),
    path("employer-engagements/<int:pk>/decide/", employer_engagement_decide, name="employer-engagement-decide"),
    path("worker-engagements/<int:pk>/", worker_engagement_review, name="worker-engagement-review"),
    path("engagements/<int:pk>/check-in/", check_in_shift, name="engagement-check-in"),
    path("engagements/<int:pk>/check-out/", check_out_shift, name="engagement-check-out"),
    path("worker-invoices/<int:pk>/", worker_service_invoice, name="worker-service-invoice"),
    path("applications/<int:application_id>/engage/", start_engagement_from_application, name="application-engage"),
    path("worker-wallet/", worker_wallet, name="worker-wallet"),
    path("employer-wallet/", employer_wallet, name="employer-wallet"),
    path("wallet/mpesa/", save_mpesa, name="wallet-mpesa"),
    path("worker-courses/", worker_courses, name="worker-courses"),
    path("worker-courses/<int:pk>/enroll/", enroll_course, name="course-enroll"),
    path("worker-settings/", worker_settings, name="worker-settings"),
    path("employer-settings/", employer_settings, name="employer-settings"),
    path("api/accounts/", include("apps.accounts.urls")),
    path("api/profiles/", include("apps.profiles.urls")),
    path("api/jobs/", include("apps.jobs.urls")),
    path("api/contracts/", include("apps.contracts.urls")),
    path("api/wallet/", include("apps.wallet.urls")),
    path("api/dashboard/", include("apps.dashboard.urls")),
    path("api/prices/", prices_api, name="api-prices"),
    path("api/maps/config/", maps_config_api, name="api-maps-config"),
    path("ussd/callback/", ussd_callback, name="ussd_callback"),
    path("", include("apps.agentic_core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
