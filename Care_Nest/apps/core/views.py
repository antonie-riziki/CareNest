from django.shortcuts import render

# Create your views here.


def index(request):
    return render(request, "index.html")


def role_selection(request):
    return render(request, "role_selection.html")
