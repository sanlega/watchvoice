# Riesgos

| # | Riesgo | Prob. | Impacto | Mitigación / estado |
|---|---|---|---|---|
| R1 | Whistle no cabe en memoria o es lento en el reloj (sin cifras para el Watch, sin unload) | Media | Alto | Gate W-023 en dispositivo; `system_dictation` por defecto si falla; modelo cargado una vez en un actor |
| R2 | Whistle exige watchOS 26.2; la app apunta a 10 | Alta | Medio | Selección en runtime; Whistle como capacidad condicional |
| R3 | Sin slice de simulador para `libneedle.a` | Cierta | Medio | Mocks + `swift test` en macOS; los tests de ASR real solo en dispositivo |
| R4 | Telemetría de Needle (README y blog se contradicen) | Media | Alto (privacidad) | Verificar con proxy antes de distribuir; bloquear W-054 hasta aclararlo |
| R5 | Inyección de prompt en la transcripción | Alta | Alto | Política independiente del texto, 422 a campos de política, detección que solo añade requisitos, recomprobación en cada tool call (pendiente en el runner) |
| R6 | La detección de capacidades sensibles por palabras clave no ve intenciones indirectas | Alta | Medio | No es la última barrera: el broker del runner autoriza cada acción real (PRD §9). Ampliar fixtures |
| R7 | Carrera de idempotencia o cast de enums en PostgreSQL sin probar | Media | Alto | La suite solo corre sobre SQLite; ejecutarla contra PG 16 (W-049, BLOCKED por Docker) |
| R8 | El rate limit por conteo en BD no es atómico | Baja | Bajo | Aceptable para un solo usuario; pasar a Redis con token bucket cuando haya Redis |
| R9 | `approval_challenges.token` guardado en claro para que el dueño lo lea | Media | Medio | Atajo documentado; entrega cifrada por dispositivo y step-up auth en W-044 |
| R10 | `instructions.text` sin cifrar en reposo | Media | Medio | W-046 (pgcrypto o cifrado en aplicación con KMS) |
| R11 | Código Swift no compilado (sin macOS/Xcode) | Cierta | Medio | CI en `macos-latest` con `swift test` y build de XcodeGen; primera ejecución pendiente |
| R12 | La app suspendida en segundo plano no drena la cola | Alta | Bajo | Drenar en foreground y al lanzar; background URLSession en W-033; no prometer background continuo |
| R13 | Emparejar el reloj sin iPhone | Media | Medio | Abierto (PRD watch §15): código de un solo uso o QR desde el dashboard |
| R14 | Distribución App Store de un `.a` de terceros más un modelo de 17 MB | Media | Medio | Decidir entre bundle y descarga con checksum (W-022) |
| R15 | Dentro de T3 Code el agente no pasa por el broker de políticas de VOX | Alta | Alto | Política y aprobaciones **antes** del despacho, worktree por run, reglas en el prompt y modo de permisos restringido por máquina (decisión del usuario) |
| R16 | El progreso depende de que el agente haga callback | Media | Medio | Timeout en servidor (W-063); el `external_ref` del hilo permite abrirlo desde T3 |
