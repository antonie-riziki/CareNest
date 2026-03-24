"""
URL configuration for Care_Nest project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

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
    # Core
    path("", index, name="home"),
    path("role-selection/", role_selection, name="role-selection"),
    # Accounts
    path("worker-signup/", worker_signup, name="worker-signup"),
    path("worker-signin/", worker_signin, name="worker-signin"),
    path("employer-signup/", employer_signup, name="employer-signup"),
    path("employer-signin/", employer_signin, name="employer-signin"),
    # Profiles
    path("worker-profile/", worker_profile, name="worker-profile"),
    path("employer-profile/", employer_profile, name="employer-profile"),
    # Dashboard
    path("worker-dashboard/", worker_dashboard, name="worker-dashboard"),
    path("employer-dashboard/", employer_dashboard, name="employer-dashboard"),
    # Jobs
    path("worker-jobs/", worker_jobs, name="worker-jobs"),
    path("employer-jobs/", employer_jobs, name="employer-jobs"),
    path("worker-job-details/", worker_job_details, name="worker-job-details"),
    path("employer-job-details/", employer_job_details, name="employer-job-details"),
    # Contracts
    path("worker-contracts/", worker_contracts, name="worker-contracts"),
    path("employer-contracts/", employer_contracts, name="employer-contracts"),
    # Wallet
    path("worker-wallet/", worker_wallet, name="worker-wallet"),
    path("employer-wallet/", employer_wallet, name="employer-wallet"),
    # Courses
    path("worker-courses/", worker_courses, name="worker-courses"),
    # Account Settings
    path("worker-settings/", worker_settings, name="worker-settings"),
    path("employer-settings/", employer_settings, name="employer-settings"),
    # API
    path("api/accounts/", include("apps.accounts.urls")),
    path("api/profiles/", include("apps.profiles.urls")),
    path("api/jobs/", include("apps.jobs.urls")),
    path("api/contracts/", include("apps.contracts.urls")),
    path("api/wallet/", include("apps.wallet.urls")),
    path("api/dashboard/", include("apps.dashboard.urls")),
    # USSD
    path("ussd/callback/", ussd_callback, name="ussd_callback"),
]
