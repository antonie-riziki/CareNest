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

urlpatterns = [
    path("admin/", admin.site.urls),
    # Core
    path("", index),
    path("role-selection/", role_selection),
    # Accounts
    path("worker-signup/", worker_signup),
    path("worker-signin/", worker_signin),
    path("employer-signup/", employer_signup),
    path("employer-signin/", employer_signin),
    # Profiles
    path("worker-profile/", worker_profile),
    path("employer-profile/", employer_profile),
    # Dashboard
    path("worker-dashboard/", worker_dashboard),
    path("employer-dashboard/", employer_dashboard),
    # Jobs
    path("worker-jobs/", worker_jobs),
    path("employer-jobs/", employer_jobs),
    path("worker-job-details/", worker_job_details),
    path("employer-job-details/", employer_job_details),
    # Contracts
    path("worker-contracts/", worker_contracts),
    path("employer-contracts/", employer_contracts),
    # Wallet
    path("worker-wallet/", worker_wallet),
    path("employer-wallet/", employer_wallet),
    # API
    path("api/accounts/", include("apps.accounts.urls")),
    path("api/profiles/", include("apps.profiles.urls")),
    path("api/jobs/", include("apps.jobs.urls")),
    path("api/contracts/", include("apps.contracts.urls")),
    path("api/wallet/", include("apps.wallet.urls")),
    path("api/dashboard/", include("apps.dashboard.urls")),
]
