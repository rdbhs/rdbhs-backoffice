from uuid import UUID

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction


class Command(BaseCommand):
    help = (
        "Explicitly link an existing active user to an Entra identity. "
        "Confirm the Object ID through an authorized Entra administrator first; "
        "email is never used. Defaults to simulation; --apply writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--tenant-id", required=True)
        parser.add_argument("--object-id", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            tenant = str(UUID(options["tenant_id"]))
            object_id = str(UUID(options["object_id"]))
            configured_tenant = str(UUID(settings.ENTRA_TENANT_ID))
        except (ValueError, TypeError, AttributeError):
            raise CommandError(
                "Tenant, Object ID and configured ENTRA_TENANT_ID must be valid UUIDs."
            ) from None
        if tenant != configured_tenant:
            raise CommandError("Tenant does not match configured ENTRA_TENANT_ID.")

        # Same canonical tenant:Graph /me.id convention as CorporateMicrosoftProvider.
        uid = f"{tenant}:{object_id}"
        User = get_user_model()
        try:
            with transaction.atomic():
                users = User.objects.all()
                if options["apply"]:
                    # Serializes different identity requests for the same user.
                    users = users.select_for_update()
                try:
                    user = users.get(pk=options["user_id"])
                except User.DoesNotExist:
                    raise CommandError("Django user does not exist.") from None
                if not user.is_active:
                    raise CommandError("Django user must be active.")

                existing = SocialAccount.objects.filter(
                    provider="microsoft", uid=uid
                ).first()
                if existing is not None and existing.user_id != user.pk:
                    raise CommandError("Microsoft identity belongs to another user.")
                if SocialAccount.objects.filter(
                    provider="microsoft", user=user
                ).exclude(uid=uid).exists():
                    raise CommandError("Django user already has another Microsoft identity.")
                if existing is not None:
                    self.stdout.write("Association already exists; no changes.")
                    return
                if not options["apply"]:
                    self.stdout.write(
                        "Simulation successful; association would be created. No changes."
                    )
                    return

                # allauth's unique(provider, uid) constraint also arbitrates requests
                # for the same external identity targeting different local users.
                account, created = SocialAccount.objects.get_or_create(
                    provider="microsoft", uid=uid, defaults={"user": user}
                )
                if account.user_id != user.pk:
                    raise CommandError("Microsoft identity belongs to another user.")
        except IntegrityError:
            raise CommandError(
                "Concurrent association conflict; no changes committed. Revalidate and retry."
            ) from None

        self.stdout.write(
            "Association created." if created else "Association already exists; no changes."
        )
