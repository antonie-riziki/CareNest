from django.urls import path
from .views import ussd_callback

urlpatterns = [
    path("callback/", ussd_callback, name="ussd-callback"),
]
