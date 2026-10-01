from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from django.views.decorators.http import require_POST

from apps.courses.models import Course, Enrollment


@login_required(login_url="worker-signin")
def worker_courses(request):
    courses = Course.objects.filter(is_active=True).order_by("title")
    enrollments = {
        e.course_id: e for e in Enrollment.objects.filter(worker=request.user).select_related("course")
    }
    return render(
        request,
        "worker_courses.html",
        {"courses": courses, "enrollments": enrollments, "enrollment_list": list(enrollments.values()), "enrolled_ids": list(enrollments.keys())},
    )


@login_required(login_url="worker-signin")
@require_POST
def enroll_course(request, pk):
    course = get_object_or_404(Course, pk=pk, is_active=True)
    enrollment, created = Enrollment.objects.get_or_create(
        course=course,
        worker=request.user,
        defaults={
            "total_fee": course.fee,
            "amount_remaining": course.fee,
            "commission_percentage": course.recovery_percentage,
            "status": Enrollment.Status.ACTIVE,
        },
    )
    if created:
        messages.success(request, f"Enrolled in {course.title}. CareNest will recover 20% of eligible earnings until KES {course.fee} is repaid.")
    else:
        messages.info(request, "You are already enrolled in this course.")
    return redirect("worker-courses")
