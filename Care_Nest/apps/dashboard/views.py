from django.shortcuts import render
from django.contrib.auth.decorators import login_required


# Create your views here.
@login_required(login_url='worker-signin')
def worker_dashboard(request):
    return render(request, "worker_dashboard.html")


@login_required(login_url='employer-signin')
def employer_dashboard(request):
    return render(request, "employer_dashboard.html")
