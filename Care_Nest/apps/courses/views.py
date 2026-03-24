from django.shortcuts import render


# Create your views here.
def worker_courses(request):
    return render(request, "worker_courses.html")
