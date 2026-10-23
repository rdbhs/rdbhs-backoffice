# Microsoft Entra ID SSO — implementación local

## Estado y bloqueo de la primera vinculación

Se integró django-allauth 65.20.1 con el proveedor Microsoft, manteniendo Django
6.1.2 y Viewflow 2.4.0. Se comprobaron inicialización, middleware, descubrimiento
del proveedor, migraciones en SQLite temporal y autenticación mediante callbacks
simulados. La App Registration **RDBHS Backoffice** ya fue creada por el operador,
con plataforma Web, single-tenant y el callback de producción indicado abajo.
Se realizaron inspecciones de producción de solo lectura; no se instaló ni
desplegó SSO ni se configuraron credenciales.

**Preparación actual: `SSO_ENABLED=False`.** No se autoriza activación. Las pruebas
unitarias habilitan el flag únicamente mediante overrides con datos ficticios.
La primera vinculación sigue siendo un pendiente funcional separado.

**SSO está deshabilitado por defecto. La primera vinculación automática está
bloqueada.** La configuración disponible no contiene una fuente autorizada que
relacione una identidad Microsoft con una cuenta Django. El proveedor devuelve
Graph `/me`: `id`, `mail` y `userPrincipalName`; los correos son mutables y no
demuestran por sí solos la titularidad de la cuenta local. Tampoco basta una
coincidencia única, un dominio corporativo o un campo `email_verified` añadido al
perfil. No se confía globalmente en correos Microsoft ni se los marca verificados.

No se implementó una vinculación insegura ni se requiere al usuario una pantalla
de conexión o un login inicial con contraseña. Sin una asociación previamente
autorizada, el acceso se rechaza. Esto es una entrega parcial segura, **no el flujo
completo de incorporación automática solicitado**.

La alternativa mínima para terminar ese flujo sin intervención del usuario es
una fuente aprobada de correspondencias `(tenant ID, Graph object ID) → usuario
Django` o `→ correo local aprobado`. Puede ser una exportación corporativa
controlada o un registro de aprovisionamiento. Hace falta definir quién la
autoriza, cómo se autentica su origen y cómo se gestionan cambios y bajas.

Si se usa el correo como paso intermedio, deben cumplirse estas condiciones:

- La fuente autorizada vincula expresamente el Object ID al correo local; no se
  deriva confianza del `mail`/UPN recibido durante el login.
- Exactamente un usuario Django coincide por correo sin distinguir mayúsculas,
  contando también duplicados inactivos para detectar ambigüedad.
- Ese usuario está activo y expresamente autorizado para SSO; no basta pertenecer
  al tenant, poseer un correo con cierto dominio o existir como `Employee`.
- Reasignaciones de correos, invitados B2B y objetos eliminados/recreados tienen
  un procedimiento de revisión. Nunca se transfiere una asociación por correo.
- La creación se realiza en una transacción, bloqueando la correspondencia y el
  usuario, verificando conflictos y usando la restricción única de la identidad.
  Un conflicto concurrente obliga a releer y aceptar únicamente al mismo dueño.

Esa fuente y el código de aprovisionamiento **no están implementados**. No debe
habilitarse el piloto esperando que usuarios sin asociación puedan entrar.

## Comportamiento implementado

- Se mantienen `/accounts/login/`, `/accounts/logout/` y `/admin/`, sus contraseñas
  y el backend `django.contrib.auth.backends.ModelBackend`.
- El botón `Continue with Microsoft` aparece solo con `SSO_ENABLED=True` y envía
  POST con CSRF. El estilo y formulario pertenecen a Viewflow; solo se sustituye
  su bloque de proveedores. Se conserva `next`, validado por Django/allauth.
- Solo se exponen las dos rutas Microsoft. No se incluye `allauth.urls` ni se
  habilitan registro, recuperación, conexión/desconexión o gestión de cuentas.
- Allauth realiza OAuth, estado de sesión, PKCE, intercambio de código y Graph.
  No hay implementación propia de intercambio ni validación manual de tokens.
- Autorización y token usan el tenant UUID configurado, nunca `common`.
  La App Registration debe ser single-tenant. La frontera de tenant depende del
  endpoint Microsoft y de esa configuración; `/me` no proporciona un claim `tid`
  verificable. No se decodifican tokens sin validarlos para inventar esa garantía.
- Una subclase mínima del proveedor cambia únicamente la extracción de identidad:
  `SocialAccount.provider = microsoft`, `uid = <tenant UUID>:<Graph object UUID>`.
  Así una asociación no sobrevive accidentalmente a un cambio de tenant. No se
  aceptan asociaciones antiguas con UID sin prefijo.
- La selección de App Registration se realiza en `get_app`, sin consultar la base
  de datos. Las entradas `SocialApp`, incluidas duplicadas o con el mismo client
  ID, se ignoran y nunca sustituyen la configuración autorizada del entorno.
  Debe existir exactamente una app en settings, con client ID, secreto y tenant
  correspondientes al entorno; un client ID distinto o endpoints alternativos
  se rechazan. No hay fallback a la base si falta la configuración en settings.
- Solo acceden cuentas activas con asociación autorizada existente. El correo
  actual del perfil no modifica usuario, contraseña, permisos ni asociación.
- Si una identidad ya tiene dueño, ese usuario sigue siendo su dueño aunque el
  correo coincida con otro usuario. Las solicitudes de conexión se rechazan y la
  restricción única `(provider, uid)` impide duplicar una identidad.
- No se crean usuarios o asociaciones en primeros logins. Por ello, dos intentos
  simultáneos desconocidos se rechazan sin una carrera de creación. La prueba
  concurrente cubre esa denegación, no una vinculación positiva aún inexistente.
- No se conceden grupos, permisos, `is_staff` ni `is_superuser`.
- Allauth actualiza `SocialAccount.extra_data` y su fecha durante la búsqueda de
  una identidad existente, antes del rechazo personalizado de usuarios inactivos.
  Estos metadatos proceden de Graph autenticado y no gobiernan identidad,
  vinculación, permisos ni activación del usuario. Se conserva ese comportamiento:
  no se identificó un riesgo de autorización que justifique reemplazar el flujo
  de la biblioteca o añadir bloqueos/transacciones. Un rechazo no garantiza cero
  escrituras de metadatos. Si se exige esa propiedad por retención o auditoría,
  deberá abordarse expresamente como otro requisito.
- No se guardan tokens; no se exige verificación por correo ni se envían avisos de
  allauth. Se conserva el backend de correo deshabilitado.
- Logout continúa siendo POST local: invalida la sesión Django, pero no cierra
  Microsoft 365 ni Superset. Otro login Microsoft puede ser inmediato.

## Variables

En `.env` local o en un archivo de entorno protegido, nunca en Git:

| Variable | Requisito |
| --- | --- |
| `SSO_ENABLED` | `False` por defecto; acepta booleanos habituales. |
| `ENTRA_TENANT_ID` | Directory/tenant ID, UUID; requerido al habilitar. |
| `ENTRA_CLIENT_ID` | Application/client ID, UUID; requerido al habilitar. |
| `ENTRA_CLIENT_SECRET` | Valor del secreto, no su ID; requerido al habilitar. |

Una configuración habilitada incompleta o inválida impide arrancar. No se
necesitan credenciales para ejecutar los tests. No poner secretos en argumentos
de comandos, capturas, logs, documentación o registros `SocialApp`.

## App Registration — creada según confirmación del operador

Confirmado por el operador: aplicación **RDBHS Backoffice**, independiente de
Superset, plataforma **Web**, cuentas solo del tenant corporativo y callback:

`https://backoffice.elcone.dev/accounts/microsoft/login/callback/`

No se inspeccionó Azure directamente. Antes de una activación autorizada faltará:

1. Confirmar tenant/client IDs, secreto vigente y responsable de rotación.
2. Confirmar permiso Microsoft Graph **delegado `User.Read`**. No permisos de aplicación,
   correo/calendarios ni `Directory.Read.All`. Consentimiento según la política
   del tenant. El proveedor Microsoft/Graph no requiere añadir scopes OIDC para
   esta implementación ni solicitar `offline_access`.
3. Usar authorization code con PKCE. No activar implicit grant ni public client.
4. Considerar exigir asignación en Enterprise Applications como defensa adicional;
   esa asignación no reemplaza la autorización local.
5. No registrar front-channel logout: no se implementó ese endpoint. El destino
   tras logout local es `/accounts/login/`.

Usar registros separados para desarrollo y producción cuando sea posible.
Para probar OAuth en localhost se necesitaría registrar adicionalmente
`http://localhost:8000/accounts/microsoft/login/callback/`; no se da por creado.
Invitados del tenant requieren autorización explícita; single-tenant no equivale
a excluir automáticamente todos los invitados.

## Preparación y validación local

Conservar el `.env` existente. Para una instalación nueva, partir de
`.env.example`, usar SQLite local y generar una `SECRET_KEY` propia. No sobrescribir
un archivo de entorno existente ni reutilizar credenciales de producción.

```bash
uv sync --locked
uv run manage.py check
uv run manage.py migrate --plan
uv run manage.py migrate
uv run manage.py runserver
```

`migrate` crea las tablas adicionales de `account` y `socialaccount`; no cambia
`auth.User` ni `Employee`. Estas migraciones se aplicaron únicamente a bases
temporales durante las pruebas, no a la base configurada en `.env`.

Con `SSO_ENABLED=False`, comprobar usuario/contraseña en `/accounts/login/` y
`/admin/`, acceso con sus permisos habituales y logout POST. Mantener el flag
deshabilitado durante toda la preparación; activación y piloto requieren otra
autorización. Sin asociaciones autorizadas el callback debe denegar acceso.

Validación reproducible sin credenciales Azure ni base persistente:

```bash
DATABASE_URL=sqlite:///:memory: SECRET_KEY=local-test-only DEBUG=True SSO_ENABLED=False \
  uv run --locked manage.py test
uv lock --check
uv pip check
```

Los tests simulan token y Graph, conservando rutas, estado, proveedor, adapter y
sesión reales de allauth. Cubren identidades vinculadas, cambio/ausencia de correo,
denegación de primera coincidencia única/ambigua/desconocida, inactivos, tenant
incorrecto, asociaciones conflictivas, concurrencia de rechazos, permisos,
redirecciones, CSRF, PKCE, logout, SSO deshabilitado y ausencia de envíos.

**No hay una prueba positiva de primera vinculación fiable** porque no existe la
fuente de confianza ni se implementó esa función. Debe añadirse junto con pruebas
de concurrencia positiva sobre PostgreSQL cuando se resuelva ese bloqueo.

Esta revisión aprobó **62 tests** (41 existentes y 21 de SSO), incluidos tres
nuevos casos para selección de App Registration. Comprobaciones satisfactorias:
`manage.py check` sin incidencias, `makemigrations --check --dry-run` sin cambios,
`uv lock --check --offline`, `uv pip check` y `git diff --check`.
Se comprobaron además seis escenarios de arranque en la validación anterior: configuración
habilitada válida, deshabilitada sin credenciales, booleano inválido, tenant
inválido, client ID ausente y secreto ausente. Django y Viewflow no se actualizaron.

## Git en el VPS: conversión inicial propuesta, no ejecutada

Se decidió usar un checkout Git directo, sin artefactos, manifiestos ni scripts
de transferencia o de configuración de secretos. La inspección de solo lectura
confirmó Git 2.43.0 y `/srv/rdbhs-backoffice/app` como directorio root-owned sin
`.git`. El servicio usa esa ruta y `.venv/bin/gunicorn`; su shebang referencia la
ruta absoluta actual. No mover ni recrear la venv durante la conversión.
El entorno permanece en `/etc/rdbhs-backoffice/backoffice.env`, root:root 0600;
no existe `.env` dentro de la aplicación. Estáticos están fuera de la app.
Se compararon los 34 archivos versionados de la revisión base: solo settings
difiere, con el hash HSTS ya documentado; no se encontraron archivos adicionales
fuera de la venv y bytecode. Repetir el inventario al convertir, pues puede cambiar.
La consulta Git desde root fue denegada por falta de una clave de host GitHub
verificada en known_hosts. No se añadió esa clave y la autenticación al repositorio
todavía no se pudo comprobar. No usar StrictHostKeyChecking=no para resolverlo.

Conversión futura, solo con autorización:

1. Aprobar y publicar un commit revisado, incluyendo el ajuste HSTS ya desplegado.
2. Resolver acceso de lectura al repositorio para el propietario del checkout:
   root. No asumir que las credenciales del operador local existen en el VPS;
   cualquier alta de claves/known_hosts requiere autorización independiente.
3. Inventariar y respaldar archivos existentes, venv, entorno y base. Comparar
   archivos con el commit aprobado; no eliminar ni sobrescribir archivos ajenos.
4. Inicializar Git **en el directorio actual**, agregar el remoto aprobado y hacer
   fetch. `git reset --mixed <SHA_APROBADO>` puede establecer HEAD e índice sin
   tocar archivos del directorio. Revisar después el diff y los archivos sin
   seguimiento, incluidos los ignorados; la app aún no corresponde al commit.
5. En ventana autorizada, parar solo Backoffice y restaurar el árbol aprobado con
   `git restore --source=<SHA_APROBADO> --worktree -- .` únicamente después de
   comprobar y respaldar todas las diferencias y colisiones. Esto materializa el
   checkout, conserva `.venv` ignorada y no modifica el EnvironmentFile externo.
   No usar `git clean`, `reset --hard` ni opciones de checkout forzado.
6. Exigir árbol versionado limpio y HEAD exacto antes de instalar, migrar y
   reiniciar. Si existe una diferencia no aprobada, detenerse; no resolverla
   descartando archivos. Mantener root como propietario y el runtime sin escritura.

La conversión de metadatos Git y la instalación de SSO son pasos distintos.
Ninguno está autorizado actualmente.

## Credenciales: configuración manual por SSH

No se creará helper ni tests de helper. Tras autorización, el operador usará una
sesión SSH privada, sin grabación de terminal/entrada sudo y sin shell tracing.
Editar directamente el EnvironmentFile con un editor seguro, deshabilitando
swap, backups y archivos de recuperación para no generar copias desprotegidas.
No pegar secretos en órdenes shell, historial, argumentos, chat ni logs.
Conservar las demás variables, agregar las tres ENTRA y mantener
`SSO_ENABLED=False`. Verificar únicamente presencia, formato y permisos sin
imprimir contenido. Un editor no evita la captura si la sesión está grabada:
en ese caso detenerse y acordar un canal aprobado. No reiniciar durante preparación.

## Despliegues Git posteriores — comandos previstos, no ejecutados

Prerrequisitos: checkout inicial completo, acceso Git resuelto, SHA publicado y
aprobado, respaldo y ventana autorizados. No desplegar con `git pull` sin fijar
revisión. Ejecutar Git/uv como root, manteniendo el ownership actual:

```bash
cd /srv/rdbhs-backoffice/app
sudo -H git status --short
# Detenerse si hay cambios; comprobar aparte archivos ignorados o colisiones.
RELEASE_BRANCH=RAMA_APROBADA
RELEASE_SHA=SHA_COMPLETO_APROBADO
sudo -H git fetch origin "$RELEASE_BRANCH"
sudo -H git cat-file -e "${RELEASE_SHA}^{commit}"
sudo -H git merge-base --is-ancestor "$RELEASE_SHA" FETCH_HEAD
sudo -H git diff --stat HEAD "$RELEASE_SHA"
sudo systemctl stop rdbhs-backoffice.service
sudo -H git switch --detach "$RELEASE_SHA"
sudo -H env UV_CACHE_DIR=/srv/rdbhs-backoffice/uv-cache \
  /home/datamanager/.local/bin/uv sync --locked --no-dev --python /usr/bin/python3
```

Reemplazar los identificadores de ejemplo. Ejecutar paso a paso, comprobando cada
salida; detenerse ante cualquier error y aplicar el rollback autorizado. No pegar
el bloque como una secuencia que continúe después de un fallo. Si Git rechaza el
checkout por una colisión, conservar los archivos y resolverla antes de continuar.

Ejecutar los comandos Django con el entorno real leído por systemd, sin copiar
secretos a la línea de comandos; por ejemplo, para revisar el plan:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --uid=rdbhs-backoffice --gid=rdbhs-backoffice \
  --property=EnvironmentFile=/etc/rdbhs-backoffice/backoffice.env \
  --working-directory=/srv/rdbhs-backoffice/app \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  /srv/rdbhs-backoffice/app/.venv/bin/python manage.py migrate --plan
```

Con el mismo contexto, ejecutar `check --deploy`; revisar y aprobar el plan antes
de `migrate --noinput` y `collectstatic --noinput`. Para esta entrega se esperan
solo nueve migraciones account y seis socialaccount. No continuar si aparece otra
operación inesperada. Verificar que `SSO_ENABLED` siga deshabilitado sin mostrar
el entorno, iniciar únicamente Backoffice y verificar login local, permisos,
funciones, estáticos y salud de Superset. No habilitar SSO en estos comandos.

## Antes de producción y rollback

- Resolver la fuente de confianza y completar el aprovisionamiento seguro antes
  de declarar satisfecho el acceso directo de usuarios nuevos.
- Probar la integración real con un registro de desarrollo y un piloto; los tests
  mock no acreditan políticas de Entra, consentimiento ni configuración remota.
- Verificar migraciones y concurrencia en PostgreSQL de pruebas. Esta entrega usa
  SQLite y no consulta producción.
- Preparar backup de base, checkout/venv y entorno. Revisar el plan de migraciones y
  conservar los cambios HSTS ya existentes en el repositorio.
- Mantener `DEBUG=False`, cookies seguras y Caddy sobrescribiendo
  `X-Forwarded-Proto`, con Gunicorn limitado a loopback. Confirmar URL HTTPS del
  callback, hosts y CSRF confiables. No se necesitan nuevos proxies ni servicios.
- Definir duración de sesión y bajas: deshabilitar Entra no elimina una sesión
  Django existente. Las bajas deben desactivar el usuario local y revocar sesiones.
  El login local conserva su disponibilidad y no está protegido por MFA de Entra.
- Asegurar acceso administrativo local y rotación del secreto antes de activar.
- Deshabilitar con `SSO_ENABLED=False` y reiniciar: desaparece el botón y las rutas
  Microsoft rechazan acceso. **Esto no revoca sesiones Microsoft ya autenticadas**;
  si se requiere, invalidarlas expresamente con un procedimiento autorizado.
- Para rollback completo, volver al commit anterior aprobado y sus dependencias,
  conservar las tablas adicionales y no revertir migraciones ni restaurar datos a
  ciegas. Durante la primera conversión, el código previo aún incluye un ajuste
  HSTS sin commit: guardar y restaurar también esa versión de settings si se
  vuelve a la instalación original, o se perdería HSTS al volver al antiguo SHA.
  Sesiones ligadas a un backend retirado pueden exigir nuevo login.

No se desplegó SSO ni se configuró Azure desde esta implementación. Crear un
commit local no autoriza su publicación, conversión del VPS ni despliegue.

## Fuentes oficiales

- [Instalación allauth](https://docs.allauth.org/en/latest/installation/quickstart.html)
- [Proveedor Microsoft](https://docs.allauth.org/en/latest/socialaccount/providers/microsoft.html)
- [Configuración social](https://docs.allauth.org/en/latest/socialaccount/configuration.html)
- [Claims e identidad Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)
- [Redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri)
