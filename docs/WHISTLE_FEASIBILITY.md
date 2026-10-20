# Whistle en Apple Watch — factibilidad

Fecha de verificación: 2026-10-20. Fuentes leídas en esa fecha (ver final). **Nada de esto se ha ejecutado en un reloj.**

## Veredicto
| Pregunta | Estado |
|---|---|
| Existe binario oficial para watchOS | **CONFIRMADO**: `needle3/watchos-arm64/libneedle.a` + `needle.h` |
| Arquitecturas | **CONFIRMADO** (cabeceras Mach-O inspeccionadas): fat `arm64_32` + `arm64`, plataforma watchOS **dispositivo** |
| Slice de simulador / xcframework | **NO EXISTE** → Whistle no se puede probar en simulador |
| watchOS mínimo del binario | **26.2** (LC_BUILD_VERSION minos 26.2, sdk 26.2) |
| API C real | **CONFIRMADA** (abajo, literal) |
| Swift package / bindings oficiales | **NO ENCONTRADO** → bridging propio |
| Benchmarks en Watch / RAM pico | **NO ENCONTRADO**. Los 11,1 ms TTFT / 1.319 tok/s son de **Apple M4 Pro**, no aplicables al reloj |
| Licencias | Runtime Apache-2.0 (GitHub `cactus-compute/needle`, HF `Cactus-Compute/needle3`); modelo Apache-2.0 (HF `Cactus-Compute/whistle`) |
| Compila/enlaza en nuestro proyecto | Verificado en CI (job `watch-device`, build para dispositivo sin firma) |
| Telemetría / red en `libneedle.a` | **No se encontró** (inspección estática, abajo) |
| Funciona en reloj (latencia, memoria, batería, WER es) | **BLOCKED**: no hay reloj físico |

## Artefactos
- Engine: `https://huggingface.co/Cactus-Compute/needle3/resolve/main/watchos-arm64/libneedle.a` (3.011.160 B) y `needle.h` (4.528 B, idéntico al de iOS). GitHub Releases de `needle` está vacío; último tag `v3.2.8` (commit 2026-10-19 "Pin engine 3.3.3").
- Modelo: `whistle.cact`, 16.919.407 B (`https://huggingface.co/Cactus-Compute/whistle`). Idiomas: en, de, fr, es, it, nl, pl.
- Fijados en `apps/watch/scripts/fetch-needle.sh` (descargados 2026-10-20, URLs a commit fijo):
  - `needle3` @ `d6c7d6a2e9655505e0f8e23339356dd1705900eb` (lastModified 2026-10-19): `needle.h` `a27dac4b3239eead91d124b7f7dbce5495c13019903fd817b68c16d39b055a99`, `libneedle.a` `f794e7996701ea83d98d0670c23aeb8c7a4519adc9526c1489f1e08046e12f9c`
  - `whistle` @ `741118687e1f008f06d1351a402a731274789790` (lastModified 2026-10-18): `whistle.cact` `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb`

## API C (extracto literal de `needle.h`)
```c
/* One process-global, non-thread-safe model per kind, text and speech.
   Negative returns indicate failure; call needle_last_error() for the specific reason. */
NEEDLE_API int needle_load(const unsigned char* cact, unsigned long long n);
NEEDLE_API const char* needle_last_error(void);
NEEDLE_API void needle_set_audio(const char* language, const char* keywords, int word_timestamps);
NEEDLE_API void needle_reset(void);
NEEDLE_API int needle_transcribe(const float* pcm, int samples, const char* language,
    const char* keywords, int word_timestamps, char* out, int out_capacity);
NEEDLE_API int needle_stream_transcribe_process(const float* pcm, int samples,
    const char* language, const char* keywords, char* out, int out_capacity);
NEEDLE_API int needle_stream_transcribe_stop(char* out, int out_capacity);
```
Implicaciones:
- Entrada: PCM **float32 mono 16 kHz en [-1, 1]**. Hasta 30 s en una pasada; más largo, ventanas de 30 s; transcripción limitada a 320 tokens.
- `language` = `"es"` o `NULL` (autodetección). `keywords` separadas por saltos de línea (keyword biasing, útil para nombres de proyecto).
- Salida: JSON en un buffer del llamador (`{"text":…,"language":…,"ttft_ms":…,"decode_tps":…}` y opcionalmente `words`). Según el `needle.h` fijado, `needle_transcribe` devuelve el **número de tokens** (negativo si falla), no un tamaño; usamos un buffer de 16 KB, de sobra para ≤320 tokens sin timestamps. Silencio o ruido → texto vacío → `asr_empty`.
- Carga **desde buffer en memoria** (no hay load-from-path), **sin función de unload/free**, **no thread-safe** y global al proceso. Por eso el wrapper es un único `actor` y el modelo (16,9 MB) se queda residente.
- Enlazar con libc++ (el `.a` referencia `__cxa_throw`, `pthread_create` y `mmap`).

## Estrategia de bridging
1. `scripts/fetch-needle.sh` descarga a `apps/watch/Vendor/needle/` (gitignored, sin binarios en git).
2. Una sola configuración, deployment target watchOS 26.2. `project.yml` enlaza `libneedle.a` + `-lc++` solo para el SDK `watchos` (`OTHER_LDFLAGS[sdk=watchos*]`) e importa `needle.h` con `CNeedle/module.modulemap`. El Swift real se compila con `os(watchOS) && !targetEnvironment(simulator)`; simulador y macOS usan el stub, que reporta `.blocked(reason)` y **nunca devuelve texto**. `whistle.cact` va en el bundle (un build de dispositivo sin él falla).
3. `WhistleTranscriber` (actor): `needle_load` una sola vez con `Data` del bundle; `needle_transcribe` fuera del main actor; los errores se mapean con `needle_last_error()`.
4. Fallback automático: si Whistle está `.blocked` o `transcribe` lanza, la app pasa al dictado del sistema con un aviso visible (`TranscriberSelector.fallbackNotice`); el audio grabado se descarta. Orden: `TranscriptionService` → `system_dictation` (dictado del sistema vía `TextField`; **el framework Speech/SFSpeechRecognizer NO está disponible en watchOS**, verificado en developer.apple.com) → `remote` solo con consentimiento explícito.

## Conflictos y dudas abiertas
- **Telemetría (R4)**: el README de GitHub dice que está activa por defecto (`NEEDLE_TELEMETRY=0`/`DO_NOT_TRACK=1`); el blog dice que el engine "no lee variables de entorno". Inspección estática del `libneedle.a` fijado (2026-10-20, ambos slices): **cero** apariciones de `http`, `://`, `getenv`, `NEEDLE_TELEMETRY`, `DO_NOT_TRACK`, `telemetry`, `socket`, `connect`, `getaddrinfo`, `gethostbyname`, `send`/`recv`, `URLSession`/`NSURL`, `CFNetwork`/`CFHTTP`, `nw_` (Network.framework), `dlopen`/`dlsym` ni `objc_msgSend`. Sus imports externos son libc/libm/pthread (`fopen`/`fread`/`fwrite`, `sysctlbyname`, `getrusage`, `pthread_create`, `munmap`…) y libc++. Conclusión: este binario **no puede abrir conexiones de red por sí mismo** (el aviso del README no aplica a este binario). Pendiente opcional: confirmarlo con un proxy en la prueba de dispositivo (#13). Si se actualiza el binario, repetir la inspección.
- watchOS 26.2 es ahora el mínimo de toda la app (decisión de #10); Series 6 o posterior.
- Memoria: no hay cifra publicada. El modelo residente (16,9 MB) más el KV/beam (5 beams) pueden chocar con los límites de memoria del reloj, que Apple no documenta públicamente.
- Distribución App Store de un `.a` de terceros más un modelo de 17 MB: revisar el tamaño y si el modelo va en el bundle o se descarga con checksum.

## Gate W-023 (a medir en dispositivo; no son cifras del fabricante)
≥95 % de sesiones de 15 s sin crash/OOM · p95 ≤ 8 s tras la captura (clip de 15 s) · memoria estable en 20 dictados · consumo aceptable con 20 comandos/día · WER en español sobre un set propio con nombres de proyecto. Si no pasa: `system_dictation` por defecto, sin cambiar UI ni API.

## Fuentes
https://www.cactuscompute.com/blog/whistle · https://cactuscompute.com/blog/needle-supported-devices · https://huggingface.co/Cactus-Compute/whistle · https://huggingface.co/Cactus-Compute/needle3 · https://github.com/cactus-compute/needle · https://developer.apple.com/documentation/speech · https://developer.apple.com/documentation/avfaudio/avaudiosession
