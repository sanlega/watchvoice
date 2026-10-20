# Ejecución en T3 Code (VPS / PC / Mac)

El backend decide (proyecto, intención, política, máquina) y T3 Code ejecuta. Cada máquina es un **runner**: un entorno T3 Code con un *webhook scheduled task* cuyo prompt es `contracts/t3code/dispatcher-prompt.md`.

```
Watch ─texto─► VOX backend ─router + política─► task + run ─outbox run.dispatch.v1─► POST firmado (HMAC)
                                                                                         │ T3 Connect relay
                                                         (retiene si la máquina está off)▼
                       GET /v1/instructions/{id}.progress ◄── POST /v1/runs/{id}/events ◄── hilo T3 (worktree vox/<run_id>)
```

## Selección de máquina
`runner_hint` del reloj → mención en el texto ("en el mac", "en mi pc", "en el servidor"; solo nombres y alias registrados, con preposición) → `projects.context_json.default_runner` → runner `is_default` del usuario.
- Una máquina desconocida o dos máquinas mencionadas → `needs_clarification` (`unknown_runner` / `ambiguous_runner`) **sin crear tarea**.
- Ningún runner configurado → la tarea queda `unassigned` (no se envía nada).
- Las ejecuciones que requieren aprobación **no se despachan** hasta que se aprueban.

## Hechos verificados de T3 Code (2026-10-20, desde este PC)
- El entorno `DESKTOP-70G3MFR` (Windows) tiene el proyecto "Musiquillas" registrado; los proyectos son por máquina.
- `schedule_task {type:'webhook'}` da una URL pública vía T3 Connect y valida firma HMAC-SHA256 (`header`, `encoding: hex`, `prefix: sha256=`, secreto vía `request_secret`). El relay retiene las peticiones si la máquina está apagada (`maxDeliveryAgeMinutes`, ≤1440). El prompt ve el body solo a través de placeholders `{{body.x.y}}`.
- `t3_thread_launch` con `workspaceStrategy: worktree` crea un hilo top-level aislado en su rama; `t3_thread_search` permite deduplicar.
- **No he encontrado una API documentada para que el backend lea el estado de un hilo T3 desde fuera.** Por eso el progreso llega por **callback cooperativo**: el agente hace `curl` al backend. Si el agente no informa, el run se queda en `provisioning/running`; el plan es un timeout del lado servidor (W-063).

## Seguridad: qué cambia respecto al plan original
- La política de VOX se aplica **antes** de despachar: lo sensible exige aprobación con challenge. **Dentro** de T3 el agente tiene los permisos de ese entorno T3 (modo de permisos, credenciales de la máquina). El broker por tool call del PRD §9 **no** existe en T3. Mitigaciones: worktree y rama por run, reglas en el prompt y modo de permisos restringido para el proyecto dispatcher. **Decisión tuya**: qué modo de permisos usar en cada máquina, sobre todo en el VPS.
- El texto dictado llega al prompt del agente: es inyección de prompt por diseño (el texto *es* la orden). Va acotado en `<untrusted>`, y las acciones sensibles siguen requiriendo una aprobación previa en VOX.
- El webhook va firmado, con secreto por máquina (variable de entorno nombrada en `runners.secret_env`; nunca en BD). Sin secreto, no se envía.
- El token de callback se deriva con `HMAC(VOX_CALLBACK_SECRET, run_id)`, vale 24 h desde el despacho y solo permite mover ese run hacia delante (estados terminales inmutables, `pr_url` solo https). El texto del agente (`summary`) se trata como no confiable y no se audita ni se registra en logs.
- La entrega es *at-least-once*: el dispatcher deduplica por `run_id` (paso 1 del prompt).

## Puesta en marcha (por máquina; pendiente de tu OK)
1. En T3 Code de la máquina: crear un proyecto "VOX Dispatcher" y pedir a un agente `schedule_task` con `{type:'webhook', maxDeliveryAgeMinutes: 1440, signature:{header:'x-vox-signature', encoding:'hex', prefix:'sha256='}}`, el prompt de `contracts/t3code/dispatcher-prompt.md`, `bindToCurrentThread: true` y el secreto vía `request_secret`.
2. En el backend: `INSERT INTO runners (user_id, name, aliases, webhook_url, secret_env, is_default) VALUES (…, 'mac', '["macbook"]', '<webhookUrl>', 'VOX_RUNNER_SECRET_MAC', false)` y exportar `VOX_RUNNER_SECRET_MAC` con el mismo secreto.
3. Backend: `VOX_PUBLIC_BASE_URL` (HTTPS alcanzable desde las máquinas), `VOX_CALLBACK_SECRET`, `python -m app.worker`.

## No verificado todavía
- Que la ruta anidada `{{body.callback.url}}` se resuelva (la doc da como ejemplo `{{body.release.tag_name}}`, así que es probable).
- La entrega real por el relay de T3 Connect y el formato exacto de la firma que espera T3 (hex + `sha256=` es lo que documenta).
- `curl` con comillas simples en Windows/PowerShell: el agente del PC puede tener que adaptar el comando.
