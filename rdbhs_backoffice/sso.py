"""Microsoft policy; email-only first-login matching is deliberately closed.

Graph /me does not attest ownership of a local Django email account. Only a
previously authorized (tenant, Graph object ID) association is accepted. OAuth
exchange, state, PKCE and redirect validation remain allauth responsibilities.
"""

from uuid import UUID

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.microsoft.provider import MicrosoftGraphProvider
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST


class ClosedAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False


class CorporateMicrosoftProvider(MicrosoftGraphProvider):
    # Retain allauth's URLs, protocol implementation and Graph integration.
    package = "allauth.socialaccount.providers.microsoft"

    def extract_uid(self, data):
        try:
            tenant = str(UUID(settings.ENTRA_TENANT_ID))
            object_id = str(UUID(data["id"]))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise PermissionDenied("Invalid corporate identity.") from error
        # Graph /me does not return tid; an unexpected one must not be trusted.
        if "tid" in data and data["tid"] != tenant:
            raise PermissionDenied("Incorrect corporate tenant.")
        return f"{tenant}:{object_id}"


class CorporateSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return False

    def can_authenticate_by_email(self, login, email):
        return False

    def is_email_verified(self, provider, email):
        return False

    def get_app(self, request, provider, client_id=None):
        # Allauth normally merges DB SocialApps with settings, which can raise
        # MultipleObjectsReturned before get_provider can validate the result.
        # Select the one authorized settings app here, including direct calls
        # from the Microsoft token and Graph adapters. Never consult the DB.
        from allauth.socialaccount.models import SocialApp

        if not settings.SSO_ENABLED or provider != "microsoft":
            raise PermissionDenied("Corporate SSO is disabled.")
        apps = settings.SOCIALACCOUNT_PROVIDERS.get("microsoft", {}).get("APPS", [])
        if len(apps) != 1:
            raise PermissionDenied("Exactly one corporate provider must be configured.")
        config = apps[0]
        if (
            client_id not in (None, settings.ENTRA_CLIENT_ID)
            or config.get("client_id") != settings.ENTRA_CLIENT_ID
            or config.get("secret") != settings.ENTRA_CLIENT_SECRET
            or config.get("settings") != {"tenant": settings.ENTRA_TENANT_ID}
        ):
            raise PermissionDenied("Invalid corporate provider configuration.")
        return SocialApp(
            provider="microsoft",
            client_id=settings.ENTRA_CLIENT_ID,
            secret=settings.ENTRA_CLIENT_SECRET,
            settings={"tenant": settings.ENTRA_TENANT_ID},
        )

    def pre_social_login(self, request, sociallogin):
        account = sociallogin.account
        if (
            not settings.SSO_ENABLED
            or account.provider != "microsoft"
            or not account.uid.startswith(f"{settings.ENTRA_TENANT_ID}:")
            or sociallogin.state.get("process", "login") != "login"
            or not sociallogin.is_existing
            or not sociallogin.user.is_active
        ):
            # No mail/UPN fallback, even for a unique email match.
            raise ImmediateHttpResponse(render(request, "sso/denied.html", status=403))

    def on_authentication_error(self, request, provider, **kwargs):
        # Default allauth pages link to account routes we intentionally omit.
        raise ImmediateHttpResponse(render(request, "sso/denied.html", status=403))


@require_POST
@csrf_protect
def microsoft_login(request):
    if not settings.SSO_ENABLED:
        raise Http404
    if (
        request.POST.get("process", "login") != "login"
        or request.GET.get("process", "login") != "login"
    ):
        raise PermissionDenied
    from allauth.socialaccount.providers.microsoft.views import oauth2_login

    return oauth2_login(request)


@require_GET
def microsoft_callback(request):
    if not settings.SSO_ENABLED:
        raise Http404
    from allauth.socialaccount.providers.microsoft.views import oauth2_callback

    return oauth2_callback(request)


def login_context(request):
    return {"sso_enabled": settings.SSO_ENABLED}
