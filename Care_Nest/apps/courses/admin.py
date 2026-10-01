from django.contrib import admin

from apps.courses.models import Course, Enrollment

admin.site.register(Course)
admin.site.register(Enrollment)
