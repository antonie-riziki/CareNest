from django.contrib import admin

from apps.jobs.models import Application, Job, JobImage


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "employer", "status", "locked", "pay", "job_type", "start_date")
    list_filter = ("status", "job_type")


admin.site.register(JobImage)
admin.site.register(Application)
