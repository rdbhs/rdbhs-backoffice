from django.contrib import admin

from .models import (
    CompanySettings,
    Employee,
    EmploymentType,
    PayrollPeriod,
    ProductivityGoal,
)


class ProductivityGoalInline(admin.TabularInline):
    model = ProductivityGoal
    extra = 0


@admin.register(EmploymentType)
class EmploymentTypeAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "default_minimum_hours", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["code", "name"]


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ["last_name", "first_name", "email", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["first_name", "last_name", "email"]
    inlines = [ProductivityGoalInline]


@admin.register(ProductivityGoal)
class ProductivityGoalAdmin(admin.ModelAdmin):
    list_display = [
        "employee",
        "employment_type",
        "minimum_hours",
        "effective_from",
        "effective_to",
    ]
    list_filter = ["employment_type", "effective_from"]
    search_fields = ["employee__first_name", "employee__last_name", "employee__email"]


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ["start_date", "end_date", "payment_date"]


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = [
        "effective_from",
        "effective_to",
        "appointment_cutoff_days",
        "appointment_cutoff_time",
        "timezone",
    ]
