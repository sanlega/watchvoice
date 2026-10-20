# ADR-0001 — Permisos de ejecución, bypass y aprobación desde el reloj

Estado: **aceptada** (2026-10-20). La sesión de grilling la respondió un agente experto y Santi la confirmó.

## Contexto
La ejecución ocurre en T3 Code (VPS, PC, Mac). T3 no permite responder sus prompts de permiso programáticamente y dentro de un hilo el agente tiene los permisos de su usuario del SO. El dueño quiere reglas granulares por máquina, poder hacer *bypass* por voz y aprobar desde el reloj con un solo botón.

## Decisiones
1. **Capas.** (a) **Barrera real:** los runners no poseen credenciales sensibles; los efectos sensibles son *acciones brokered* que ejecuta VOX tras la política o la aprobación. La primera es `repo.merge_protected`; `deploy.production` llegará cuando se conozca el canal del bot del VPS. (b) `runtimeMode` de T3 por run: bypass → `full-access`, granular → `auto`; sin verificar, requiere un spike por versión de T3. (c) Cooperativa: el agente pide aprobación por API. Solo (a) es frontera de seguridad, y la documentación y la UI lo dicen así.
2. **Reglas.** El ámbito es `runner × project` (NULL = comodín) → `mode: bypass | granular`, y en granular `capability → allow | ask | deny`. Gana la más específica; en empate, la más restrictiva (deny > ask > allow). Una capacidad no mencionada cae al ámbito más amplio, nunca a `allow`. Las reglas pueden tener `expires_at`.
3. **Bypass.** Quita las aprobaciones de VOX y lanza `full-access`. Se mantienen auditoría, presupuestos y timeouts. **Suelo** no anulable por voz ni por reloj: `secrets.exfiltrate`, `backup.delete`. Solo es real porque esas credenciales viven únicamente en VOX.
4. **Voz.** Endurecer se aplica al instante. Relajar crea una aprobación `policy_change` que repite el ámbito en claro. Las negaciones y los huecos (ámbito o duración ausentes al relajar) → aclaración. "siempre" = riesgo alto. Un LLM solo podría rellenar huecos, nunca elegir entre relajar y endurecer.
5. **Un toque.** Las aprobaciones se firman con una clave P-256 del Secure Enclave del reloj (CryptoKit, watchOS 6+) sobre `{approval_id, action_fingerprint, decision, device_id, ts}`; desfase máximo de ±2 min. Cada aprobación se decide una sola vez. Las altas caducan a los 5 min y el resto a los 15. El reloj exige código de desbloqueo para aprobar las altas.
6. **Push.** APNs directo al reloj (requiere Apple Developer Program de pago). Categorías: `VOX_APPROVAL_LOW` [Aprobar (Doble Toque), Rechazar] y `VOX_APPROVAL_HIGH` [Revisar]. El payload no lleva secretos. Niveles: time-sensitive para aprobaciones, `needs_input` y política; active para failed y timed_out; passive para succeeded y stale.
7. **Peticiones de agentes.** `POST /v1/runs/{id}/approval-requests`. En bypass se aprueba al instante (auditado). Máximo 3 pendientes y 20 por run. El resumen se sanea a 60 caracteres y se marca como escrito por el agente.
8. **Parar.** `POST /v1/runs/{id}/cancel` y `POST /v1/pause`, ambos firmados y de un toque. Envían el webhook `run.cancel.v1` (caduca en 1 h) y el dispatcher llama a `t3_thread_interrupt` sobre el hilo trabajador. Es best-effort.
9. **Timeouts.** Plazo de 2 h por run, heartbeat cada ≤10 min, `stale` a los 30 min sin eventos (un solo push) y `timed_out` al vencer el plazo, con cancelación.
10. **Emparejado.** `python -m app.admin pair` genera un código de 8 caracteres sin ambigüedades, de un solo uso, válido 10 min y que se invalida tras 5 fallos. El reloj solo pide el código (URL por defecto `vox.santi.tools`). Reemplazar un dispositivo exige `--replace`.
11. **Administración.** Mientras no haya dashboard, se hace por CLI con SSH interactivo del dueño. El bot del VPS solo puede desplegar y consultar el estado. Cada comando de administración se audita.
12. **Confianza del runner.** `trust: isolated | cooperative`. El PC es `cooperative` (T3 corre con el usuario de Santi) y las tarjetas lo avisan. Por defecto `ask`; si el dueño dice bypass, se respeta.
13. **VPS.** Usuarios del SO separados para backend, runner T3 y bot de despliegue; el runner no tiene sudo ni grupo docker. Postgres no se publica y los backups van fuera de la máquina. Cloudflare Tunnel; Cloudflare Access para administración. Despliegue de tags con pytest previo, rollback por tag y migraciones como paso separado.

## Desviaciones deliberadas
- Relajar sin duración pide aclaración en vez de aplicarse "a esta tarea": una frase de política aislada no tiene tarea asociada.
- El `challenge_token` de v0.3 se sustituye por la firma del dispositivo, porque el contrato aún no se ha publicado.
