# API delta v0.2.0 → v0.4.0 (solo aditivo)

**v0.4.0 (ADR-0001):** `POST /devices/pair`, `PUT /devices/me/push-token`, `POST /runs/{id}/approval-requests` (+ GET), `POST /runs/{id}/cancel` (ahora con cuerpo firmado), `POST /pause`, `GET /policy`. La decisión de aprobación pasa a ir firmada (`ts`, `signature`, `action_fingerprint`) y el `challenge_token` de v0.3, que nunca se publicó, desaparece. `Approval` añade `kind`, `agent_provided`, `action_fingerprint`, `runner` y `runner_trust`. `InstructionStatus` añade `applied`. Migración `004_permissions.sql`.

## v0.3.0

Contrato canónico: `contracts/openapi.yaml` (v0.3.0). Original intacto en `docs/vox-agent-spec/openapi.yaml`.
Inventario real: **en este repo no había backend implementado**, solo la especificación v0.2. No existe ningún cliente desplegado que romper, pero todo se diseña como si el ESP32 estuviera en producción.

## Añadido e implementado (`backend/`)
| Operación | Notas |
|---|---|
| `POST /v1/instructions` | Auth `DeviceAuth` + scope `instructions:write`. Requiere `Idempotency-Key` (8–128). Devuelve 202 **después** del commit (instrucción + evento + outbox + registro de idempotencia en una sola transacción). Misma clave y mismo cuerpo → 202 con el mismo `instruction_id` y `Idempotent-Replayed: true`. Misma clave **o** mismo `client_instruction_id` con otro cuerpo → 409. Errores: 400 sin clave, 401, 403, 413 (>16 KiB), 422 (incluye campos desconocidos como `execution_mode`), 429 con `Retry-After`. |
| `GET /v1/instructions/{id}` | Scope `instructions:read`. 404 si la instrucción no es del usuario. |
| `GET /v1/approvals?status=` | Parámetro opcional nuevo. Marca como `expired` las aprobaciones vencidas al leerlas. |
| `POST /v1/approvals/{id}/decision` | `challenge_token` opcional en el esquema; el servidor lo exige si la aprobación lo tiene (todas las nuevas). 403 si el challenge no es válido, 409 si ya no está pendiente o ha caducado. Repetir la misma decisión es idempotente. |

Campos opcionales nuevos en `Approval`: `project_id`, `risk`, `challenge_token`.

`InstructionStatus`: `queued | routing | needs_clarification | captured | dispatched | awaiting_approval | failed | cancelled`. El brief no incluía `captured` ni `awaiting_approval`; los añado porque "idea anotada" y "bloqueada por política" no pueden presentarse como `dispatched` sin mentir al usuario.

`transcription.engine`: `whistle | system_dictation | remote | manual | mock`. El brief decía `apple_speech`, pero el framework Speech no existe en watchOS.

## Propuesto, no implementado (siguientes tickets)
- `POST /v1/instructions/{id}/resolve` `{project_id | create_project, expected_version}` (W-047). `instructions.version` ya existe para el control optimista.
- `GET /v1/watch/bootstrap` (feature flags, motores ASR, keywords de alias para biasing) (W-048).
- `GET /v1/projects?limit=`, `GET /v1/tasks?source=apple_watch` (W-043).

## Base de datos — `backend/migrations/002_instructions.sql`
Se aplica sobre `schema.sql` v0.2 (001). Reutiliza tablas equivalentes en lugar de duplicarlas:
- Los relojes son filas de `devices` (`hardware_model='apple_watch'`). La tabla `watch_devices` del brief no se crea. Columna nueva `devices.scopes jsonb DEFAULT ["recordings:write"]`, así el ESP32 sigue siendo solo audio.
- La idempotencia usa la `api_idempotency` existente.
- Tablas nuevas: `instructions` (UNIQUE `user_id, client_instruction_id`) e `instruction_events` (sin texto). (`approval_challenges` existió en el borrador v0.3 y se eliminó en v0.4.)
- `routing_decisions.recording_id` pasa a admitir NULL, con un CHECK XOR contra la nueva columna `instruction_id`; todas las filas existentes cumplen el CHECK.
- `tasks.instruction_id` UNIQUE: es la garantía de que no se duplican tareas.
- `device_delivery_attempts` del brief queda **diferida**: los intentos se registran en el reloj y el servidor ya deduplica.

Down: `002_instructions.down.sql` (destructivo; solo sin datos reales).

## Validación
- 001→004 aplicadas sobre PostgreSQL 16 con `deploy/migrate.sh`, que es idempotente. La suite completa pasa sobre PG (`VOX_TEST_PG`), incluido un test de 8 envíos concurrentes idénticos (1 instrucción, 1 tarea). Al ejecutarla sobre PG apareció un bug real (bind de texto a columnas enum), ya corregido.
