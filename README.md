# RDBHS Backoffice

## Authentication

The application uses `django-allauth 65.19.7` with Django's standard User,
permissions, groups, and sessions. The account apps and Microsoft provider are
installed, with `AccountMiddleware` and both Django's `ModelBackend` and
allauth's authentication backend. No business models or CRUD permissions change.

- `/accounts/login/`: branded Tabler page, Microsoft first and local
  username/password second. Successful login returns to a safe requested `next`
  URL or `/employees/`.
- `/accounts/logout/`: sidebar POST with CSRF; GET only shows confirmation.
  Logout ends the Django session and returns to login. It does not terminate the
  Microsoft browser session.
- `/accounts/microsoft/login/`: POST starts the provider's authorization-code flow.
- `/accounts/microsoft/login/callback/`: provider callback, verified against the
  installed provider URL configuration and Django's `microsoft_callback` route.
- `/accounts/3rdparty/`: authenticated account connections. Sign in locally, then
  use Account connections in the sidebar to associate Microsoft explicitly.
- `/admin/`: retains the standard Django Admin username/password login.

### Local setup

Run `uv sync`, then `uv run python manage.py migrate` against your configured
application database to create allauth's account/social-account tables. These are
package migrations; there are no changes to the business model schema.
Local Django users created through Admin or `createsuperuser` continue to work.

Copy the three blank Microsoft settings from `.env.example` into your ignored
`.env` file, then restart the server. All three values are required to enable
Microsoft sign-in. Without them, local login remains available and the Microsoft
button is disabled. Credentials are configured exclusively in settings from the
environment; do not also create a Microsoft SocialApp in Admin, which would
introduce an ambiguous duplicate provider configuration.

### Microsoft Entra registration

1. In Microsoft Entra admin center, open **App registrations → New registration**.
   Choose **Accounts in this organizational directory only (Single tenant)**.
2. Under **Authentication**, add the **Web** platform. For local development on
   `http://localhost:8000`, register exactly:
   `http://localhost:8000/accounts/microsoft/login/callback/`.
   Use that same hostname and port when opening the application. For production,
   register `https://<your-backoffice-host>/accounts/microsoft/login/callback/`.
3. From **Overview**, copy **Application (client) ID** into
   `MICROSOFT_CLIENT_ID` and **Directory (tenant) ID** into `MICROSOFT_TENANT_ID`.
   The tenant must be the UUID, not `common`, `organizations`, or `consumers`.
4. Under **Certificates & secrets → Client secrets**, create a secret and copy
   its **Value**, not its secret ID, into `MICROSOFT_CLIENT_SECRET`.
5. Ensure Microsoft Graph's delegated **User.Read** permission is available.
   Complete consent according to your tenant's policy. The installed Microsoft
   provider requests this scope and calls Graph `/v1.0/me` to obtain the profile;
   this application contains no custom OAuth, token-validation, or Graph code.
6. Leave implicit access-token and ID-token flows disabled. The provider uses
   authorization code with the tenant-specific `/oauth2/v2.0/authorize` and
   `/oauth2/v2.0/token` endpoints.

The production callback is built by allauth from the incoming request's origin.
Your deployment must supply the correct HTTPS scheme and host, including trusted
proxy configuration when relevant.

References: [allauth Microsoft provider](https://docs.allauth.org/en/latest/socialaccount/providers/microsoft.html),
[Entra app registration](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app),
[redirect URI setup](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri),
and [client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials).

### Accounts and authorization

Microsoft sign-in creates or signs in a normal Django user associated with an
allauth SocialAccount. New users receive no staff/superuser flags, groups, or
business permissions automatically, and are not linked to Employee records.
An administrator must assign the required Django permissions/groups.

Automatic email authentication and automatic connection are explicitly disabled.
The Microsoft Graph provider does not establish a verified-email claim by default.
An email matching an existing local user's email does not log into or merge that
user. With unique email enabled, allauth stops automatic signup and sends the
identity to its signup flow; attempting to reuse the same email is rejected.
Sign in to the existing local account and explicitly connect Microsoft through
Account connections instead. This also preserves existing groups and permissions.
No custom linking adapter is used. Standard allauth account/signup routes remain
available; local self-registration, like social registration, grants no business
permissions. Email verification is disabled to preserve existing local login;
this does not make Microsoft email addresses trusted for automatic linking.

See [allauth social account configuration](https://docs.allauth.org/en/latest/socialaccount/configuration.html)
for the documented email authentication, auto-connection, and POST login defaults.

### Validation

Run `uv run python manage.py check` and `uv run python manage.py test`.
Automated tests cover local authentication, incorrect credentials, logout, CSRF,
redirect safety, CRUD permissions, email collisions, and the generated Microsoft
authorization URL/callback with test-only credentials. They do not contact
Microsoft. A real SSO smoke test remains necessary after registering the app and
supplying credentials.
