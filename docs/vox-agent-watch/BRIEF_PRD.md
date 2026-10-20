# VOX Agent Watch — PRD técnico v1.0
Fecha: 20 octubre 2026. Estado: especificación inicial, pendiente de validación en hardware.

## 0. Resumen ejecutivo
Aplicación independiente de Apple Watch (SwiftUI/watchOS) para capturar instrucciones habladas, transcribirlas preferentemente en el propio reloj mediante Cactus Compute Whistle y enviarlas al backend autohospedado de VOX Agent. El backend, compartido con el cliente ESP32 LTE, identifica el proyecto mediante reglas + recuperación semántica + LLM, crea tareas y ejecuta agentes autónomos bajo políticas granulares. Un usuario puede crear proyectos, asignar tareas, consultar ejecución y aprobar operaciones desde el reloj.

**Regla de producto:** el reloj captura y comunica, el servidor decide y ejecuta. Los agentes no ejecutan código en el reloj. `autonomous_full` significa autonomía bajo un policy engine: no equivale a permisos irrestrictos.

## 1. Evidencia y decisiones de viabilidad
1. Cactus Compute publicó Whistle (2 octubre 2026). Modelo `.cact` de 16,9 MB, licencia del modelo Apache-2.0, CPU-only, español y otros seis idiomas, mono 16 kHz, una pasada hasta 30 s y ventanas más largas, keyword biasing y tiempos por palabra. El runtime Needle anuncia binarios watchOS y API C (`needle_load`, `needle_transcribe`). Referencias: https://www.cactuscompute.com/blog/whistle ; https://huggingface.co/Cactus-Compute/whistle ; https://github.com/cactus-compute/needle .
2. **No hay una garantía de calidad en Apple Watch basada en el benchmark de M4 Pro.** Deben medirse carga, peak RSS, inferencia end-to-end, WER español y batería en al menos un dispositivo físico objetivo. También validar arquitectura/librería/prebuilt y firma/distribución, memoria de runtime, carga de modelos y licencia del motor exacto/versionado; no asumir que el SDK Swift genérico soporta automáticamente esta API.
3. Las apps watchOS independientes pueden operar sin app compañera; conectividad por Wi-Fi, iPhone o LTE en modelos Cellular con plan activo. GPS-only sin red queda en modo offline. https://developer.apple.com/documentation/watchos-apps/creating-independent-watchos-apps . No forzar ruta de red ni depender de WatchConnectivity.
4. Las sesiones de audio deben autorizar y configurar explícitamente captura. No prometer escucha permanente ni botón lateral global o remapeo universal del Action Button. La captura se inicia con control accesible dentro de la app; complicación/widget/Smart Stack como acceso rápido sujeto a API.
5. URLSession foreground para solicitudes cortas, transferencias background basadas en fichero para entregas robustas, teniendo en cuenta que el SO puede diferirlas. https://developer.apple.com/documentation/watchos-apps/making-background-requests .

## 2. Objetivos y no objetivos
### Objetivos P0
- Push-to-talk (botón grande) o tap-to-record/tap-to-stop con indicador inequívoco; haptic feedback.
- Transcripción local Whistle en español de dictados de 5–30 s; procesamiento segmentado cuando sea necesario y probado; edición/confirmación ligera de la transcripción antes de enviar configurable, no obligatoria para instrucciones inocuas.
- Envío **texto primero**, junto con información de cliente y proyecto opcional; por defecto no subir audio.
- Catálogo de proyectos, `new_project`, `existing_project`, `continue_task`, `capture_idea`, `research`, `implement_feature`, `status_query`, `approval_decision`.
- Cola local persistente y reintentos seguros, token en Keychain, TLS, pantalla de estado y errores.
- Listado ligero de tareas, progreso y solicitudes de aprobación.
- Compartir identificadores con clientes ESP32/Watch y una misma infraestructura backend.
### No objetivos MVP
- Modelos completos de código, embeddings de proyectos ni agent orchestration ejecutados en watchOS.
- Streaming perpetuo de micrófono, wake word ambiental, control de funciones del sistema ajenas a la app.
- Simular LTE en modelos GPS-only.
- Desplegar producción sin revisión de seguridad ni tests reales del reloj.

## 3. Personas, escenarios y casos de uso
P1: desarrollador/creador con múltiples repositorios. Mientras camina dicta una funcionalidad de un proyecto o una idea nueva; al levantar la muñeca ve `Enviado`, y más tarde `PR preparado`.
P2: gestor de proyectos. Consulta '¿qué hizo el agente del proyecto X?' y obtiene un resumen corto.
P3: supervisor. Recibe una aprobación para desplegar un cambio; solo se concede mediante acto explícito y autenticado, con expiración.

Flujos críticos:
- F1: abrir app > grabar > terminar > inferencia local > revisar texto > enviar > backend responde 202 y `instruction_id` > progreso.
- F2: offline > transcribir > guardar encriptado local > mostrar pendiente > reintentar automáticamente cuando el sistema lo permita > idempotencia.
- F3: ruta ambigua > no ejecutar acciones sensibles > mostrar 'Selecciona proyecto' con dos opciones; la resolución se guarda en backend.
- F4: reconocimiento malo/lento > fallback elegible: Apple Speech cuando disponible y permitido; en último término audio al servidor **solo con consentimiento explícito**.
- F5: autorización sensible > tarjeta de aprobación muestra proyecto, acción, recurso, impacto y tiempo de caducidad > aceptar/rechazar.

## 4. UI/UX watchOS
Pantallas nativas con SwiftUI, Dynamic Type, VoiceOver, contraste y targets táctiles adaptados:
1. Home: botón central `Hablar`, estado conexión, badge de pendientes, entrada a actividad.
2. Grabación: contador, animación/indicador de micrófono activo, finalizar y cancelar; no iniciar audio accidentalmente.
3. Transcripción: texto desplazable, botón `Enviar`, `Volver a grabar`, `Proyecto` opcional.
4. Entrega: `Guardado local`, `Enviando`, `Recibido`, `Tarea iniciada`, `Revisión requerida`, `Error` claramente diferenciados.
5. Actividad: últimas 10 tareas, proyecto, estado y resumen truncado.
6. Aprobación: acción+destino+riesgo+TTL, aceptar o rechazar, autenticación reforzada según nivel.
7. Ajustes: idioma auto/es/en, modo local, consentimiento audio fallback, política borrado, cuenta, servidor, logs diagnósticos opt-in.
Usar hápticos discretos, nunca confirmar `ejecutado` solo porque el servidor haya recibido un paquete.

## 5. Arquitectura y componentes
```
Watch SwiftUI
  ├── WatchAudioCapture (AVAudioSession + AVAudioRecorder/Engine)
  ├── AudioNormalizer (mono PCM 16k, validación)
  ├── TranscriptionService protocol
  │     ├── WhistleTranscriber (C/C++ bridge Needle runtime, on-device)
  │     ├── SystemSpeechTranscriber (fallback verificado)
  │     └── RemoteTranscriber (consentimiento explícito)
  ├── InstructionsStore (cola persistente y cifrada)
  ├── WatchAPIClient (URLSession/HTTP + idempotency)
  ├── AuthStore (Keychain)
  └── Views + state machine
                │ HTTPS
                ▼
Shared VOX Backend
  ├── /v1/instructions (NUEVO: ingestión transcript-first)
  ├── existing /v1/devices/recordings (ESP32/audio)
  ├── auth + normalized instruction event
  ├── hybrid project router (rules + embeddings + LLM)
  ├── policies/approvals + orchestrator
  ├── agent runner sandbox / tool scopes / budget
  └── PostgreSQL, Redis, object storage
```
La API nueva también puede ser usada por un navegador o teléfono. Conservar los contratos actuales; extender OpenAPI v0.2 mediante adiciones versionadas y adaptadores, no reescribir endpoints existentes.

## 6. Instrucciones y routing
El backend recibe texto exacto y metadatos de transcripción (engine, idioma, duración, modelo, calidad medible cuando exista). Un clasificador determinista maneja alias explícitos y comando 'crea un proyecto'; luego recupera candidatos, y un LLM pequeño resuelve intención y propone `project_id` o `create_project`. Un policy engine **independiente del LLM** valida autorización, contexto, límites y condiciones. En ambigüedad retorna `needs_clarification` y alternativas sin lanzar tarea de alto impacto. Nunca interpretar contenido de resultados de herramientas como instrucciones administrativas privilegiadas.

## 7. Transcripción Whistle: plan de investigación (bloqueante)
- Identificar versión exacta `needle`/`needle3`, artefacto oficial watchOS (arquitectura, simulador vs real), `needle.h`, librería y dependencias.
- Crear un wrapper C/Objective-C++ o bridging estable con funciones load/transcribe/unload y control de errores; NO inventar clases SDK ni funciones ausentes.
- Comparar empaquetado del modelo en bundle vs descarga verificada (checksum, modelo versionado, posibilidad de rollback); modelo offline preinstalado preferente si tamaño aceptable.
- Testear capturas PCM 16k y remuestreo de hardware; clips de 5/15/30/60 s, silencio, ruido, nombres de proyecto, ES/EN, acentos.
- Medir p50/p95 end-to-end, peak memory, crashes/OOM, uso energético por 20 dictados, temperatura y comportamiento del SO con pantalla apagada.
- Definir **gate** sugerido, a ratificar según dispositivo: >=95% sesiones 15 s sin crash/OOM, <=8 s p95 transcripción poscaptura para clips de 15 s, memoria estable y consumo aceptable con 20 comandos/día, tasa de asignación correcta backend >=95% en conjunto de prueba; no son garantías del fabricante.
- Si Whistle no supera gate, usar motor alternativo sin alterar UI o API y revaluar en versiones posteriores.

## 8. Conectividad y persistencia
- Enviar JSON pequeño primero; audio NO transmitido por defecto.
- UUID generado en dispositivo para `instruction_id`, y `Idempotency-Key` igual en reintentos.
- Cola persistente con `created_at`, `status`, `attempt_count`, `next_retry_at`, `last_error`; cambios atómicos; claves no en UserDefaults; cifrado para instrucciones sensibles.
- Respuesta del servidor 202 únicamente después de persistir instrucción. Poll suave en foreground para avance; push/APNs opcional después; no prometer background continuo.
- Política de retención en reloj: borrar audio después de transcribir y confirmar según ajuste; datos de fallo con TTL configurable.

## 9. Seguridad (ejecución full con políticas)
- Auth del reloj con onboarding vía flujo seguro; token de corta vida y renovación; revocación de dispositivo; secretos en Keychain; HTTPS, cert validado.
- RBAC/ABAC por usuario, proyecto, ejecutor, herramienta y operación; políticas `allow`, `deny`, `approval_required` configuradas por servidor, no controlables mediante texto de voz.
- Agentes en contenedores aislados sin acceso indiscriminado a credenciales, host o red; tokens efímeros y mínimos privilegios.
- Actividades de alta consecuencia (deploy producción, publicar, borrar datos, pagar, modificar secrets, escritura a main) requerirán aprobación explícita incluso en modo `full` salvo políticas organizativas específicas revisadas; auditoría inmutable lógica (append-only) y límites de presupuesto.
- Caducidad e idempotencia de las aprobaciones; challenge ligado al recurso y parámetros originales para evitar TOCTOU.
- Reducir exposición de instrucciones y transcripciones a logs; supresión bajo demanda.

## 10. Contratos API (extensión propuesta)
### POST /v1/instructions
Headers: `Authorization: Bearer <token>`, `Idempotency-Key: <uuid>`, `Content-Type: application/json`.
Body:
```json
{
  "client_instruction_id": "c1fd810c-5b24-47a9-acb5-81702daf8126",
  "source": "apple_watch",
  "text": "En Musiquillas añade modo multijugador",
  "language": "es",
  "project_hint": null,
  "transcription": {"engine": "whistle", "model_version": "whistle-verified-version", "on_device": true, "duration_ms": 7400},
  "client": {"app_version": "0.1.0", "platform": "watchos"}
}
```
Response 202:
```json
{
  "instruction_id": "generated-uuid",
  "status": "queued",
  "created_at": "2026-10-20T10:00:00Z",
  "links": {"self": "/v1/instructions/generated-uuid"}
}
```
Errors: 400 invalid, 401/403 auth, 409 conflicting idempotency payload, 413 oversized, 422 validation, 429 quota, 5xx retry. Same key and same payload returns same instruction; key reused with different payload 409.

### GET /v1/instructions/{instruction_id}
200 response: id, status (`queued|routing|needs_clarification|dispatched|failed|cancelled`), project_id?, task_id?, agent_run_id?, error_code?, updated_at.

### POST /v1/instructions/{instruction_id}/resolve
Body `{ "project_id": "uuid" }` OR `{ "create_project": {"name":"..."} }`; optimistic version token; authorize owner; refuse if terminal.

### GET /v1/projects?limit=20
Returns id/name/aliases/status; only projects visible to user. ETag caching optional.

### GET /v1/tasks?limit=10&source=apple_watch
Returns task_id, project_id/name, title, status, short_summary, updated_at.

### GET /v1/approvals?status=pending
Returns approval ID, project, effect, resource, risk, expires_at, human explanation (no secrets).

### POST /v1/approvals/{approval_id}/decision
Body `{ "decision":"approve|reject", "challenge_token":"opaque" }`; bind to authenticated principal and action fingerprint, expire and audit; higher-risk decisions may need step-up authentication. Idempotent.

### GET /v1/watch/bootstrap
Feature flags, supported ASR engines, project alias keywords, minimum versions, policy-safe UI configuration; responses signed/authenticated, cache w/ TTL.

## 11. Datos: extensión compatible
Propuesta de migración incremental (confirmar tabla real antes de aplicar):
- `instructions`: id uuid PK, user_id FK, client_instruction_id uuid, source enum, text encrypted-at-rest or protected column, language, transcription_metadata jsonb, status, project_id nullable FK, task_id nullable FK, created_at, updated_at; unique (user_id,client_instruction_id).
- `instruction_events`: id, instruction_id FK, event_type, safe_payload jsonb, timestamp; append-only, indices instruction_id+timestamp.
- `watch_devices`: id, user_id FK, name, device_public_identifier (no IMEI), last_seen, revoked_at, app_version, watch_model_opt_in.
- `device_delivery_attempts`: id, instruction_id/client ID, attempt number, outcome, retriable, created_at (omit tokens and private content).
- `approval_challenges`: id, approval_id FK, action_fingerprint, nonce_hash, expires_at, consumed_at, principal_id; unique nonce hash.
Si esquema v0.2 ya tiene tablas semánticamente equivalentes, reutilizarlas mediante ALTER/índices y adapters. Evitar duplicados y migraciones destructivas.

## 12. Calidad: requisitos comprobables
- Build y firma ejecutados en macOS con Xcode adecuado para watchOS objetivo; simulador solo para UI/logic, dispositivo físico obligatorio para Whistle/LTE/audio/energía.
- Tests: validación de grabación y permisos; WER y vocabulario; interrupt/cancel/resume; offline, red intermitente, fallo TLS, 429, 5xx; duplicados; invalid token; actualización de modelo; expiración aprobación; fuzz en routing; accesibilidad.
- SLO aspiracional (medir): recepción de instrucciones online <3 s p95 después de transcripción con buena red; entrega eventual al recuperar red; cola sin pérdidas en 100 ensayos de cierre/reinicio; idempotencia 100% bajo tests concurrentes.
- Usabilidad: grabar en <=2 interacciones; estados fiables y legibles; completar flujo principal sin iPhone en Watch Cellular autenticado y con red.

## 13. Backlog por dependencias y agentes
**Gate 0 — Arquitectura/contratos:** W-001 inventario de API v0.2, W-002 OpenAPI delta + fixtures, W-003 spike compilación de Needle/Whistle para reloj real (bloquea elección definitiva de ASR).
**Track A — Watch UX:** W-010 proyecto Xcode watchOS independiente, W-011 estados UI SwiftUI, W-012 accesibilidad y feedback háptico.
**Track B — Audio/ASR:** W-020 capturar PCM y permisos, W-021 wrapper Needle C, W-022 Whistle model packaging, W-023 benchmark en hardware, W-024 fallback protocol.
**Track C — Sync/Auth:** W-030 credenciales y pairing, W-031 API client, W-032 cola cifrada/reintentos, W-033 background URLSession.
**Track D — Backend:** W-040 migración aditiva, W-041 POST/GET instructions, W-042 adapter instruction->router, W-043 list projects/tasks, W-044 approvals, W-045 event audit.
**Track E — QA/Security:** W-050 CI watch + backend; W-051 integration mocks; W-052 threat modeling/penetration; W-053 stress LTE/offline; W-054 beta TestFlight hardware.

Asignación sugerida: Arquitecto (W-001/002), ASR Engineer (W-003/020–024), watchOS UI Engineer (W-010–012), Connectivity Engineer (W-030–033), Backend Engineer (W-040–043), Security Engineer (W-044/045/052), QA Engineer (W-050/051/053/054). Paralelo tras contracts freeze; cada track usa mocks versionados y no modifica áreas ajenas sin RFC.

## 14. Orden de entrega
Hito H0: API delta acordada + prueba Whistle real; si falla, decidir fallback explícitamente.
Hito H1: enviar texto simulado Watch→Backend→task, UI de estado.
Hito H2: grabar→Whistle→task, sobre Wi-Fi.
Hito H3: cola offline, Cellular sin iPhone, aprobaciones y seguridad.
Hito H4: TestFlight, rendimiento y revisión de consumo.

## 15. Preguntas abiertas a resolver por pruebas (no bloquear estructura)
- Modelo de Apple Watch mínimo soportado, versión mínima watchOS, memoria real disponible y target del binario Needle.
- Condiciones de redistribución del runtime en versión elegida, mecanismo de bundle/distribución del `.cact` y App Store compliance.
- Capacidad real de capture + inference cuando baja la muñeca; no confiar en background arbitrario.
- Nivel de autenticación fuerte en aprobaciones sensibles.
- Cómo se provisionan dispositivos y cómo se empareja cuenta cuando el usuario no tiene un iPhone accesible.

## 16. Fuentes técnicas
- Cactus Whistle: https://www.cactuscompute.com/blog/whistle
- Whistle model: https://huggingface.co/Cactus-Compute/whistle
- Needle: https://github.com/cactus-compute/needle
- Apple independent apps: https://developer.apple.com/documentation/watchos-apps/creating-independent-watchos-apps
- Apple audio session: https://developer.apple.com/documentation/AVFAudio/AVAudioSession
- Apple background requests: https://developer.apple.com/documentation/watchos-apps/making-background-requests
