# VOX Agent Watch — arquitectura

Regla: **el reloj captura y comunica; el servidor decide y ejecuta.** El reloj envía texto, nunca audio salvo consentimiento explícito.

```
apps/watch (watchOS, independiente, sin app iOS)        backend/ (FastAPI)
┌──────────────────────────────────────────┐            ┌───────────────────────────────────────────┐
│ SwiftUI views ── RecordingStateMachine   │            │ POST /v1/instructions                      │
│   AVAudioEngineCapture (16 kHz mono f32) │            │   auth device+scope → validar → digest     │
│   TranscriberSelector                    │            │   idempotencia (api_idempotency + UNIQUE)  │
│     WhistleTranscriber (actor, VOX_WHISTLE)│          │   TX: instruction + event + outbox → 202   │
│     system_dictation (TextField)         │   HTTPS    │ processor (background / drain_outbox)      │
│     RemoteTranscriber (consentimiento)   │ ─────────► │   HybridRouter: reglas → retriever → LLM   │
│   FileInstructionRepository (cola)       │  JSON +    │   policy.evaluate (independiente del LLM)  │
│   DeliveryCoordinator (backoff, mismo ID)│ Idempotency│   task + run (UNIQUE instruction_id)       │
│   URLSessionWatchAPIClient               │    -Key    │   o approval + challenge (TTL 15 min)      │
│   KeychainAuthSession                    │            │ GET /v1/instructions/{id}, /v1/approvals   │
└──────────────────────────────────────────┘            └───────────────────────────────────────────┘
                                                          PostgreSQL (001 v0.2 + 002) · Redis/obj. storage: futuros
```

## Decisiones
1. **Transcript-first, aditivo.** `/v1/instructions` convive con `/v1/devices/recordings` (ESP32). Las dos rutas desembocan en las mismas `routing_decisions`/`tasks`, porque ambas tablas aceptan `recording_id` XOR `instruction_id`.
2. **202 tras persistir.** La API nunca enruta en la petición. Instrucción y evento de outbox se guardan en la misma transacción; el procesado es idempotente (solo procesa `queued`, y `tasks.instruction_id` es UNIQUE). Si el router se cae, la instrucción queda `queued` y `drain_outbox` la retoma.
3. **El router propone y la política decide.** El router (reglas deterministas → `TokenOverlapRetriever` → `LLMRouter` opcional con validador ≥0,90 sobre candidatos visibles) se abstiene ante la ambigüedad (`needs_clarification`, sin tarea). `policy.evaluate` usa solo grants del servidor. El texto solo puede **añadir** requisitos (detección de capacidades sensibles), nunca quitarlos. Los campos de política en el cuerpo se rechazan con 422.
4. **`full` ≠ sin límites.** Los grants por defecto cubren leer, escribir en rama, PR, CI y shell en sandbox. Producción, borrado, pagos, secretos, push a main, publicar paquetes y mensajes externos requieren aprobación, salvo un grant `standing` explícito y vigente. El runner debe volver a comprobar la política en cada tool call; esto es solo la primera puerta.
5. **Aprobaciones con challenge.** Cada aprobación tiene un nonce (hash en BD) ligado al `action_digest`. La decisión exige el token, se consume una sola vez y caduca. Esto protege contra TOCTOU: si la acción cambia, hace falta otra aprobación.
6. **Identidad del reloj.** Es una fila de `devices` con `scopes`. El token es opaco y en BD solo se guarda su sha256. En el reloj vive en Keychain (`AfterFirstUnlockThisDeviceOnly`).
7. **Privacidad.** Los logs son JSON con ids, estado y códigos, nunca texto ni tokens (hay un test). `instruction_events.safe_payload` no contiene texto. El cifrado en reposo de `instructions.text` está pendiente (W-046).
8. **Conectividad.** La gestiona watchOS (Wi-Fi, iPhone o celular). La app no fuerza la ruta de red ni usa WatchConnectivity. Cola local persistente, con el mismo UUID y la misma `Idempotency-Key` en todos los reintentos.
9. **ASR intercambiable.** Se usa `TranscriptionService` + `TranscriberSelector`. Whistle es experimental y queda detrás del flag `VOX_WHISTLE`; sin el binario reporta `.blocked(reason)` y nunca inventa texto.
10. **Ejecución en T3 Code.** Cada máquina (VPS, PC, Mac) es un `runner` con webhook firmado; el backend elige la máquina y T3 lanza el hilo en un worktree; el progreso vuelve por `POST /v1/runs/{id}/events`. Ver `docs/T3CODE_EXECUTION.md`.

## Máquina de estados de la UI
`idle → recording → transcribing → reviewing → savedLocally → sending → received → {taskStarted | needsReview | awaitingApproval | captured | failed}`.
Un 202 solo lleva a `received`. `taskStarted` requiere que el GET devuelva `dispatched`.

## Ownership (A1–A7)
| Agente | Rutas |
|---|---|
| A1 Contracts | `contracts/`, `backend/migrations/`, `docs/API_DELTA.md` |
| A2 watch UI | `apps/watch/VoxWatch/` (vistas, AppModel) |
| A3 ASR | `apps/watch/VoxWatchCore/Sources/**/{Whistle,Mock,TranscriberSelector}*`, `AVAudioEngineCapture.swift`, `scripts/fetch-needle.sh` |
| A4 Sync | `apps/watch/VoxWatchCore/Sources/**/{URLSession*,Keychain*,File*Repository,DeliveryCoordinator}*` |
| A5 Backend | `backend/app/{main,processor,router,schemas,db,runners,worker}.py`, `contracts/t3code/` |
| A6 Security | `backend/app/policy.py`, aprobaciones/challenges, auditoría |
| A7 QA/CI | `backend/tests/`, `apps/watch/VoxWatchCore/Tests/`, `.github/workflows/` |
