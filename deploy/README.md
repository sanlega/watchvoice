# Despliegue del backend en el VPS (ADR-0001 §13)

Lo hace el bot del VPS siguiendo estos pasos. Las operaciones de administración (vincular el reloj, registrar runners, reglas permanentes) las ejecuta **el dueño por SSH**, nunca el bot.

## Una vez
1. Usuarios del SO separados: `vox` (backend, grupo `docker`), el usuario de T3 Code (sin sudo ni `docker`) y el bot de despliegue.
2. Auditar al usuario de T3: no debe tener `gh auth`, claves SSH con permisos de push a ramas protegidas, `~/.cloudflared`, `.env` con secretos de producción ni credential helpers de git.
3. `/etc/vox/` (propietario `vox`, modo 700):
   - `pg_password` (600)
   - `vox.env` (600):
     ```
     VOX_DATABASE_URL=postgresql+psycopg://vox:<pg_password>@postgres/vox
     VOX_PUBLIC_BASE_URL=https://vox.santi.tools
     VOX_CALLBACK_SECRET=<64 hex>
     VOX_RUNNER_SECRET_PC=<igual al del webhook de T3 del PC>
     VOX_RUNNER_SECRET_VPS=<igual al del webhook de T3 del VPS>
     TYPESAFE_API_KEY=<clave de console.typesafe.ai>   # router Jev (ADR-0002); sin ella, reglas deterministas
     # Push (requiere Apple Developer Program de pago):
     VOX_APNS_TEAM_ID=...  VOX_APNS_KEY_ID=...  VOX_APNS_TOPIC=<bundle id de la app del reloj>
     VOX_APNS_KEY_PATH=/run/apns/AuthKey.p8
     ```
   - `apns/AuthKey.p8` (opcional)
4. Cloudflare Tunnel: `vox.santi.tools` → `http://127.0.0.1:8080`. Cloudflare Access en cualquier ruta futura de administración; las rutas `/v1/*` usan token o HMAC propios.
5. Backups de Postgres fuera de la máquina (p. ej. `pg_dump` cifrado a un bucket con *object lock*). Las credenciales para borrarlos no pueden estar en el VPS.

## Cada despliegue (un tag de git)
```sh
cd /opt/vox && git fetch --tags && git checkout <tag>
cd deploy
VOX_TAG=<tag> docker compose build api            # el build ejecuta pytest; si falla, no hay imagen
VOX_TAG=<tag> docker compose --profile migrate run --rm migrate   # paso aparte; si falla, el stack anterior sigue
VOX_TAG=<tag> docker compose up -d api worker
curl -fsS http://127.0.0.1:8080/healthz
```
Rollback: `VOX_TAG=<tag anterior> docker compose up -d api worker`. Las migraciones son aditivas, así que el código anterior sigue funcionando con el esquema nuevo.

## Alta inicial (dueño, por SSH)
```sh
docker compose exec api python -m app.admin user-add --email santilega@gmail.com
docker compose exec api python -m app.admin runner-add --email santilega@gmail.com --name pc --alias "mi pc" \
  --webhook-url "<webhookUrl del T3 del PC>" --secret-env VOX_RUNNER_SECRET_PC --trust cooperative   --agent claude=claudeAgent:claude-opus-5-5:Claude --agent codex=codex:gpt-6.1-sol:Codex
docker compose exec api python -m app.admin runner-add --email santilega@gmail.com --name vps --alias servidor \
  --webhook-url "<webhookUrl del T3 del VPS>" --secret-env VOX_RUNNER_SECRET_VPS --default
docker compose exec api python -m app.admin pair --email santilega@gmail.com --name "Apple Watch"
```

## Verificado en local (2026-10-20)
- `migrate.sh` aplica 001→004 sobre PostgreSQL 16 y es idempotente: una segunda ejecución no cambia nada.
- La imagen `runtime` compila, y su etapa de test ejecuta la suite completa en Python 3.12.
- La suite completa pasa también sobre PostgreSQL 16 real (`VOX_TEST_PG=postgresql://…`).
- **No verificado:** el despliegue en el VPS, el túnel, APNs real y el relay de T3 contra un backend público.
