from viewflow.urls import DeleteViewMixin, DetailViewMixin, ModelViewset

from .models import Employee, EmploymentType, ProductivityGoal


class EmployeeViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
    model = Employee
    app_name = "employees"
    title = "Employees"
    icon = "people"
    list_columns = ("last_name", "first_name", "email", "is_active")
    list_filter_fields = ("is_active",)


class EmploymentTypeViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
    model = EmploymentType
    app_name = "employment-types"
    title = "Employment Types"
    icon = "work"
    list_columns = ("code", "name", "default_minimum_hours", "is_active")
    list_filter_fields = ("is_active",)


class ProductivityGoalViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
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
