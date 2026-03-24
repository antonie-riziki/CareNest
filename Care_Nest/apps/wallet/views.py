from django.shortcuts import render


# Create your views here.
def worker_wallet(request):
    return render(request, "worker_wallet.html")


def employer_wallet(request):
    return render(request, "employer_wallet.html")
