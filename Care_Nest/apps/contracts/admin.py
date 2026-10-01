from django.contrib import admin

from apps.contracts.models import Contract


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "worker", "employer", "approval_status", "chain_status", "amount")
    list_filter = ("approval_status", "chain_status")
