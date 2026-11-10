# Piloto CI/CD · RDBHS Backoffice

Primera etapa: CI y ensayo local/desechable de publicación con Ansible. No contiene credenciales, inventario de producción ni un workflow que se conecte al VPS. No crea usuarios ni modifica servicios systemd reales.

## Flujo

1. Un push a main o PR ejecuta CI sobre PostgreSQL 17: checks, detección de migraciones faltantes y suite existente. También se puede iniciar CI manualmente.
2. Tras éxito se exporta una revisión Git exacta; solo se incluyen archivos de aplicación permitidos. Los cambios locales, `.env`, credenciales, entornos virtuales y herramientas de publicación quedan fuera.
3. Se publica `application.tar.gz` con `release.json`, commit y hashes del archivo y lockfile; retención inicial siete días. No es el almacenamiento definitivo de versiones para el DRP.
4. El workflow manual recibe el ID de una ejecución CI exitosa originada por push a main del mismo repositorio. Descarga ese artefacto ya existente, sin reconstruirlo. CI iniciado manualmente sirve para diagnóstico, pero no es aceptado como origen del ensayo manual.
5. Ansible instala en `/tmp/rdbhs-cicd-*`, prepara un entorno bloqueado, migra una base sintética, recoge estáticos, activa un enlace y comprueba Gunicorn por socket Unix privado.
6. Se ejecuta la suite del artefacto instalado. Se instala el mismo paquete en una segunda carpeta y se induce un fallo posterior a la activación para comprobar recuperación del enlace y HTTP 200 de la versión anterior.

El ensayo de dos carpetas con el mismo artefacto valida los mecanismos de activación y reversión, no compatibilidad entre dos versiones de negocio diferentes. La reversión no deshace migraciones. El playbook es una prueba local deliberadamente limitada, no un rol productivo/idempotente de administración de servidores.

## Ensayo desde WSL

Herramientas: Python 3.12, uv 0.9.17, ansible-core 2.19.3, curl y PostgreSQL 17 con initdb/pg_ctl/createdb. El usuario no debe ser root. No hace falta usar una base existente ni credenciales reales.

Desde un checkout con los scripts del piloto:

```bash
python3 deploy/build_release.py --revision HEAD --output /tmp/rdbhs-bundle-NOMBRE-UNICO
python3 deploy/rehearse.py /tmp/rdbhs-bundle-NOMBRE-UNICO \
  --expected-commit "$(git rev-parse HEAD)" --report /tmp/rdbhs-pilot-result.json
```

El paquete se crea desde Git, no desde archivos sin confirmar. El ensayo crea un clúster PostgreSQL privado sin escucha TCP, con datos sintéticos y socket privado. Detiene y retira su entorno al terminar. En CI usa exclusivamente el servicio PostgreSQL desechable con nombre/usuario predefinidos. No cargar `.env` de producción ni pasar secretos a este ensayo.

## Antes de publicar en producción

- Integrar y ejecutar estos workflows en GitHub; el ensayo local no prueba facturación, permisos o disponibilidad del runner de GitHub.
- Mantener las acciones externas fijadas por SHA completo y revisar sus actualizaciones. Las revisiones iniciales se consultaron en los repositorios oficiales.
- Confirmar presupuesto y retención; guardar las versiones aprobadas para recuperación más allá de los siete días de artefactos CI.
- Implementar y probar el receptor SSH limitado con identidad de despliegue separada. No usar `datamanager` y su sudo general desde Actions.
- Crear el rol Ansible de preparación/reconstrucción del servidor y validar usuario dedicado, propiedad root del código y secretos externos.
- Añadir copia previa recuperable, exclusión de despliegues concurrentes, revisión de migraciones y activación sobre el esquema real de releases. Resolver estáticos compartidos del despliegue actual.
- Probar configuración productiva, Microsoft Entra y salud pública en un entorno separado. El ensayo apaga SSO y no acredita la integración Entra.
- Mantener el inicio manual de la publicación; no convertir cada push en un despliegue productivo.

La infraestructura y los respaldos siguen siendo partes separadas del DRP. Los artefactos CI no contienen base de datos, secretos ni archivos persistentes.
