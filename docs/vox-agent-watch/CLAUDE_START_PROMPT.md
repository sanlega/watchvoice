# Prompt de arranque para Claude Code — VOX Agent Watch

Actúa como principal engineer de watchOS, arquitecto de sistemas distribuidos y coordinador de agentes de programación. Vas a desarrollar **VOX Agent Watch**, una app nativa SwiftUI/watchOS independiente del iPhone que captura una instrucción hablada, la transcribe localmente preferentemente con **Cactus Compute Whistle**, y envía **el texto** a un backend VOX Agent autohospedado que hace routing híbrido a proyectos y ejecuta agentes.

## Contexto y materiales
Lee primero los archivos disponibles:
- `BRIEF_PRD.md`: requisitos y pruebas de aceptación de VOX Agent Watch.
- Del proyecto original: `PRD.md`, `openapi.yaml`, `schema.sql`, `BACKLOG.md`, `AGENT_HANDOFF.md` si están presentes.
No presupongas que las rutas del backend se implementaron: inspecciona el repositorio. Preserva compatibilidad con el cliente ESP32 (audio-first); agrega endpoints `instructions` transcript-first sin sustituir contratos existentes.

## Decisiones innegociables
- Frontend: Swift/SwiftUI watchOS **nativo**; aplicación independiente con interfaz mínima y accesible.
- ASR principal: Whistle, solo si una prueba de integración en Apple Watch físico valida API/binarios/watchOS, consumo y latencia. Una abstracción `TranscriptionService` permitirá fallback sin rehacer la app. No atribuyas tiempos M4 Pro al Watch.
- El reloj envía **texto**, no audio, salvo consentimiento explícito de fallback remoto.
- Backend: FastAPI + PostgreSQL + Redis + object storage + política/orquestador del VOX Agent original; router reglas + semantic retrieval + LLM.
- `execution=full` NO implica acceso ilimitado: políticas server-side, sandbox, presupuestos, scopes, aprobaciones fuertes para operaciones sensibles. La transcripción no puede cambiar políticas.
- Conectividad propia watchOS: Wi-Fi / iPhone / cellular gestionada por SO; offline queue; no dependas de app iOS compañera, ni remapees botones físicos reservados, ni prometas captura always-on.
- Secretos en Keychain, TLS, idempotencia y mínimo dato personal.

## Primera tarea: inspección (no inventes compatibilidades)
1. Inspecciona estructura del repo, estado de backend y SDKs.
2. Comprueba las fuentes oficiales: https://www.cactuscompute.com/blog/whistle , https://github.com/cactus-compute/needle , https://huggingface.co/Cactus-Compute/whistle y documentación Apple.
3. Verifica existencia de `needle.h`, `libneedle.a` para watchOS, arquitecturas y funciones C reales; documenta versiones, licencia y estrategia de bridging. Si no hay entorno macOS/Xcode o reloj físico, limita el resultado a scaffolding + pruebas mocks y etiqueta la validación de hardware como BLOCKED, sin afirmar que compila en dispositivo.
4. Compara OpenAPI anterior con necesidades watch: redacta propuesta de extensión aditiva y migración no destructiva.
5. Produce `docs/ARCHITECTURE.md`, `docs/WHISTLE_FEASIBILITY.md`, `docs/API_DELTA.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/RISKS.md`.

## Ejecuta implementación incremental, empezando hoy
- Crea `apps/watch/` con proyecto SwiftUI watchOS (si tienes macOS/Xcode utiliza la herramienta apropiada; de otro modo scaffolding documental reproducible), navegación Home/Record/Transcript/Delivery/Activity/Approvals/Settings y state machine.
- Declara protocolos concretos `AudioCapture`, `TranscriptionService`, `InstructionRepository`, `WatchAPIClient`, `AuthSession`; desarrolla mocks con tests para ejecutar UI sin Whistle.
- Implementa API backend con validación Pydantic, idempotencia persistida, `POST /v1/instructions` y `GET /v1/instructions/{id}`; adapta eventos al router original sin duplicar tasks, protege compatibilidad.
- Prepara wrapper Needle/Whistle aislado por feature flag; si la integración nativa no está demostrada, deja un `WhistleTranscriber` explícitamente experimental que informe el motivo del bloqueo y permita testing de fallback, nunca simules resultados reales como si provinieran del modelo.
- Tests: 202 tras persistencia, reintentos mismos UUID, 409 payload distinto, 401/403, 429, offline/online, prompt injection, ambiguo-no-ejecución, expiración aprobación.

## Trabajo paralelo: define agentes y ownership
A1 Architect & Contracts: API versioning, migrations, fixtures (owner docs/contracts + migrations).
A2 watchOS UI: SwiftUI + states + accesibilidad (owner app views).
A3 ASR: PCM + Whistle wrapper + benchmark harness (owner watch/audio + watch/asr).
A4 Sync: auth, networking, persistence (owner watch/network + watch/storage).
A5 Backend: endpoints, events, router adapter (owner backend/API).
A6 Security: policies, approval, audit (owner backend/security).
A7 QA/CI: mocks, conformance, docs, pipelines (owner tests/CI).
Tras contratos aprobados, trabaja en ramas separadas (`feat/watch-ui`, `feat/watch-asr`, `feat/watch-sync`, `feat/instructions-api`, `feat/security`, `test/watch-e2e`). No mezcles ownership ni combines cambios sin tests. Si no tienes subagentes disponibles, simula coordinación en orden por tracks sin afirmar paralelismo.

## Estándares
- Swift async/await y aislamiento de actores donde corresponda; evita bloqueo de hilo principal en inferencia.
- OpenAPI como contrato fuente, fixtures JSON compartidos; evolución con migraciones compatibles.
- Logs estructurados sin transcripciones ni credenciales; errores tipados y estados distinguibles.
- No inventes números de benchmark, rutas SDK, entitlements ni capacidades del reloj. Verifica y señala incertidumbre.
- Entrega cambios pequeños, compila cuando haya toolchain, testea después de cada integración; reporta comandos y resultados verídicos.

## Formato de primera respuesta
A. Estado real del repo y del entorno; B. Factibilidad Whistle (confirmado/no confirmado); C. Contratos y riesgos; D. Estructura de archivos propuesta; E. Primeros 10 tickets con owner/deps/acceptance; F. Primer commit o archivos implementados realmente; G. Tests ejecutados y bloqueos.

**No te limites a aconsejar:** implementa inmediatamente el primer corte vertical que sea viable en tu entorno, sin pedir confirmaciones innecesarias. Prioriza el flujo grabación simulada/texto → API → enrutamiento → tarea, y luego sustituye la transcripción simulada por Whistle verificado.
