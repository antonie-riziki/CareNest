from django.shortcuts import render


# Create your views here.
def worker_contracts(request):
    return render(request, "worker_contracts.html")


def employer_contracts(request):
    return render(request, "employer_contracts.html")
