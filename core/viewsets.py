from django.core.exceptions import ValidationError
from django.urls import reverse
from viewflow.urls import DeleteViewMixin, DetailViewMixin, ModelViewset

from .models import Employee, EmploymentType, ProductivityGoal
from .views import EmployeeDetailView, ProductivityGoalCreateView


class EmployeeViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
    detail_view_class = EmployeeDetailView
    model = Employee
    app_name = "employees"
    title = "Employees"
    icon = "people"
    list_columns = ("last_name", "first_name", "email", "is_active")
    list_filter_fields = ("is_active",)
    list_search_fields = ("first_name", "last_name")


class EmploymentTypeViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
    model = EmploymentType
    app_name = "employment-types"
    title = "Employment Types"
    icon = "work"
    list_columns = ("code", "name", "default_minimum_hours", "is_active")
    list_filter_fields = ("is_active",)


class ProductivityGoalViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
    create_view_class = ProductivityGoalCreateView
    model = ProductivityGoal
    app_name = "productivity-goals"
    title = "Productivity Goals"
    icon = "flag"
    list_columns = (
        "employee",
        "employment_type",
        "minimum_hours",
        "effective_from",
        "effective_to",
    )
    list_filter_fields = ("employment_type",)
    list_search_fields = ("employee__first_name", "employee__last_name")

    def get_employee_context(self, request):
        employee_pk = request.GET.get("employee")
        if not employee_pk:
            return None
        try:
            employee = Employee.objects.filter(pk=employee_pk).first()
        except (ValueError, ValidationError):
            return None
        employee_viewset = next(
            viewset
            for viewset in self.parent.viewsets
            if getattr(viewset, "model", None) is Employee
        )
        if employee and employee_viewset.has_view_permission(request.user, employee):
            return employee
        return None

    def get_success_url(self, request, obj=None):
        if request.resolver_match.url_name in ("add", "change"):
            employee = self.get_employee_context(request)
            if employee is not None:
                return reverse("core:employees:detail", args=[employee.pk])
        return super().get_success_url(request, obj=obj)
