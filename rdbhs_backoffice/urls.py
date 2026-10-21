"""
URL configuration for rdbhs_backoffice project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from viewflow.contrib.auth import AuthViewset
from viewflow.urls import Application, Site

from core.viewsets import (
    EmployeeViewset,
    EmploymentTypeViewset,
    ProductivityGoalViewset,
)

core = Application(
    title="RDBHS Backoffice",
    base_template_name="core/base_page.html",
    app_name="core",
    icon="health_and_safety",
    viewsets=[
        EmployeeViewset(),
        EmploymentTypeViewset(),
        ProductivityGoalViewset(),
    ],
)

site = Site(
    title="RDBHS Backoffice",
    primary_color="#2F7D5A",
    secondary_color="#4E6E64",
    viewsets=[core],
    urlpatterns=[
        path("", RedirectView.as_view(pattern_name="core:index"), name="index"),
        path(
            "accounts/",
            AuthViewset(allow_password_change=False, with_profile_view=False).urls,
        ),
    ],
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", site.urls),
]
