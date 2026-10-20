# Plan de implementación

Hitos (BRIEF_PRD §14). H0: contrato + prueba Whistle real. H1: texto simulado Watch→Backend→tarea. H2: grabar→Whistle→tarea sobre Wi-Fi. H3: offline, celular, aprobaciones, seguridad. H4: TestFlight y consumo.

**Estado a 2026-10-20:** la parte backend de H1 está hecha y testeada (SQLite). El cliente watch está en scaffolding con mocks, sin compilar. El lado Whistle de H0 está BLOCKED (falta hardware y Xcode).

## Primeros 10 tickets
| # | Ticket | Owner | Deps | Aceptación | Estado |
|---|---|---|---|---|---|
| 1 | W-002 Contrato v0.3 + fixtures | A1 | — | `contracts/openapi.yaml` v0.3.0 solo aditivo; los fixtures validan contra los modelos; test de conformidad verde | **Hecho** |
| 2 | W-040 Migración 002 aditiva | A1/A5 | 1 | 001+002 aplican sobre PG 16 vacío y sobre BD v0.2 con datos; down funciona sin datos | Escrita; **validación PG BLOCKED** |
| 3 | W-041 POST/GET instructions | A5 | 1,2 | 202 tras commit; replay devuelve el mismo id; 409 si cambia el payload; 400/401/403/413/422/429 | **Hecho** (17 tests) |
| 4 | W-042 Adapter instrucción→router→tarea | A5 | 3 | Sin tareas duplicadas al redeliverar; ambiguo → `needs_clarification` sin tarea; captura → sin run | **Hecho** (reglas + retriever; LLM con interfaz, sin proveedor) |
| 5 | W-044a Política + aprobación con challenge | A6 | 4 | Una inyección de prompt no evita `approval_required`; challenge obligatorio y de un solo uso; expirada → 409 | **Hecho** (primera puerta; recomprobación en el runner pendiente) |
| 6 | W-010/011 App watchOS + máquina de estados | A2 | 1 | `xcodegen` genera el proyecto; la app navega Home/Record/Transcript/Delivery/Activity/Approvals/Settings con mocks; 202 ≠ tarea iniciada | Scaffolding; **sin compilar** |
| 7 | W-031/032 Cliente API + cola persistente | A4 | 1 | Mismo UUID y misma clave en reintentos; 409/422 son fallo permanente; 429 respeta Retry-After; 401 detiene el drenado | Scaffolding + tests XCTest; **sin ejecutar** |
| 8 | W-021 Wrapper Needle (experimental) | A3 | — | Con `VOX_WHISTLE` enlaza en dispositivo watchOS 26.2; sin el flag reporta `.blocked` y nunca devuelve texto | Stub hecho; **enlace BLOCKED** |
| 9 | W-023 Benchmark en hardware | A3 | 8 | Informe con p50/p95, RSS pico, crashes/OOM, batería en 20 dictados y WER en español sobre un set propio; decisión de gate | **BLOCKED** (sin reloj) |
| 10 | W-050 CI | A7 | 3,6 | Job Linux: pytest backend. Job macOS: `swift test` VoxWatchCore + `xcodegen` + `xcodebuild` para el simulador sin Whistle | Workflow escrito; **primera ejecución pendiente** (sin push) |

## Ejecución en T3 Code (añadido 2026-10-20)
| Ticket | Owner | Estado |
|---|---|---|
| W-060 Runners + selección de máquina (hint > texto > proyecto > default) | A5 | **Hecho** (tests) |
| W-061 Despacho firmado HMAC vía outbox + reintentos | A5 | **Hecho** (tests con relay fake; relay real sin probar) |
| W-062 `POST /v1/runs/{id}/events` + `progress` en GET instruction | A5/A6 | **Hecho** (tests) |
| W-063 Timeout de runs sin callback | A5 | **Hecho** (sweeper: stale 30 min, timed_out a las 2 h) |
| W-064 Instalar dispatcher webhook en PC/VPS/Mac (`contracts/t3code/dispatcher-prompt.md`) | Usuario + A7 | **PC hecho y verificado** (smoke firmado); VPS y Mac pendientes |
| W-065 E2E real: reloj → backend → T3 (PC) → callback → reloj | A7 | Bloqueado por W-064 y por un backend con HTTPS público |

## Permisos y aprobaciones (ADR-0001, rama `feat/permissions`)
| Ticket | Estado |
|---|---|
| W-070 Reglas bypass/granular por máquina × proyecto, suelo no anulable | **Hecho** (tests) |
| W-071 Cambios de política por voz (endurecer al instante; relajar con un toque; aclaración en ambigüedad) | **Hecho** (tests) |
| W-072 Aprobaciones firmadas P-256 (Secure Enclave), TTL 5 min (alto) o 15 min | **Hecho** en backend; reloj escrito por un subagente, sin compilar |
| W-073 Push APNs (token auth, categorías, niveles) | **Hecho** en backend con APNs simulado; envío real **BLOCKED** (membresía y .p8) |
| W-074 Peticiones de aprobación de agentes, cuotas, bypass inmediato | **Hecho** (tests) |
| W-075 Parar run firmado + `run.cancel.v1` con caducidad + pausa global | **Hecho** (tests) |
| W-076 Emparejado por código + CLI de administración | **Hecho** (tests) |
| W-077 Despliegue: Dockerfile con pytest, compose, `migrate.sh` | **Hecho** y verificado en local con PostgreSQL 16; despliegue en el VPS pendiente (canal del bot) |
| W-078 Spike de `runtimeMode` en un runner desechable | Pendiente |
| W-079 Acciones brokered: `repo.merge_protected` vía la API de GitHub | Pendiente |

## Siguientes
W-046 cifrado de `text` en reposo · W-047 `/resolve` con versión optimista · W-048 `/watch/bootstrap` · W-043 projects/tasks ligeros · W-033 background URLSession · W-030 emparejamiento sin iPhone · W-045 auditoría append-only con trigger · W-052 threat model.

## Ramas
Ahora: `feat/h1-vertical-slice` (gate 0 + corte H1). Cuando el contrato esté aprobado, el trabajo se reparte en `feat/watch-ui`, `feat/watch-asr`, `feat/watch-sync`, `feat/instructions-api`, `feat/security` y `test/watch-e2e`. Cada rama toca solo las rutas de su owner (ver ARCHITECTURE.md) y no se integra sin tests verdes.

## Comandos
```sh
cd backend && pip install -r requirements-dev.txt && python -m pytest -q
python -m app.devtools sqlite:///./vox-dev.db      # imprime token del reloj
VOX_DATABASE_URL=sqlite:///./vox-dev.db uvicorn --factory app.main:create_app
cd apps/watch && ./scripts/fetch-needle.sh && xcodegen   # macOS
cd apps/watch/VoxWatchCore && swift test                  # macOS
```
