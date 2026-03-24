from django.shortcuts import render


# Create your views here.
def worker_wallet(request):
    return render(request, "workers_wallet.html")


def employer_wallet(request):
    return render(request, "employers_wallet.html")
