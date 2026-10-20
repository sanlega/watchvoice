# VOX Agent Watch (watchOS)

An independent watchOS app (no iOS companion). You speak or dictate an instruction, review the transcript, and send it to `POST /v1/instructions` (contract: `contracts/openapi.yaml` v0.4). The app then follows routing and T3 Code run progress by polling `GET /v1/instructions/{id}` while it is in the foreground.

## Layout

- `VoxWatchCore/`: a Swift package with all non-UI logic (models, API client, Keychain, outbox, delivery, state machine, ASR selection, Whistle wrapper). The Xcode project also builds these same sources as a watchOS framework.
- `VoxWatch/`: the SwiftUI app (views, `AppModel`, `AVAudioEngineCapture`, the "Dictar a VOX" App Intent and Siri/Atajos phrases).
- `VoxWidgets/`: the watch face complication. A tap opens `vox://record`, which starts recording.
- `CNeedle/module.modulemap`: exposes `needle.h` to Swift. Only the Whistle configs use it.
- `scripts/fetch-needle.sh`: downloads `needle.h`, `libneedle.a` and `whistle.cact` into `Vendor/needle/`, which is gitignored.

## Generate the Xcode project

```sh
brew install xcodegen
cd apps/watch && xcodegen
open VoxWatch.xcodeproj
```

## Run the logic tests (macOS)

```sh
cd apps/watch/VoxWatchCore && swift test
```

The tests decode every contract fixture (copies live in `Tests/VoxWatchCoreTests/Fixtures/`; re-copy them when `contracts/fixtures` changes). They also cover:

- the state machine: a 202 receipt never means "task started"
- canonical JSON (byte-equal to Python `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`), signatures verifying with the public key, and mock decide/cancel/pause flows
- delivery: retries reuse the same Idempotency-Key, 409/413/422 fail permanently, 429 honours Retry-After, an offline outbox delivers once back online, 401 halts the queue
- the Whistle stub, the ASR selector and `APIError` retriability

## Pairing

1. On the server: `python -m app.admin pair` (add `--replace` to replace an existing watch). It prints an 8-character code (no 0/O/1/I/L), single use, valid 10 min, invalidated after 5 failures.
2. On the watch: set a **watch passcode** first. The signing key is stored `WhenPasscodeSetThisDeviceOnly`, so pairing fails without one.
3. Ajustes → Vincular → type the code → Vincular. The server URL defaults to `https://vox.santi.tools/v1`; change it under Ajustes → Avanzado.
4. The watch sends its P-256 public key (Secure Enclave; a software key only where the enclave is unavailable, e.g. the simulator) and stores the returned token and `device_id` in the Keychain.

Approvals, "Parar" (cancel run) and "Pausar todo" are signed with that key over canonical JSON (sorted keys, no spaces, UTF-8; see `CanonicalJSON`), matching `backend/app/devicesig.py`. The clock must be within ±2 min of the server. High-risk approvals also need a watch passcode at tap time.

## Push notifications

- Needs a **paid Apple Developer Program** membership: free teams cannot sign the `aps-environment` entitlement (set in `project.yml`, generated into `VoxWatch/VoxWatch.entitlements`).
- The backend needs an APNs auth key: `VOX_APNS_KEY_PATH` (.p8), `VOX_APNS_KEY_ID`, `VOX_APNS_TEAM_ID`, `VOX_APNS_TOPIC=com.santilega.vox.watchkitapp`; the watch only sends its token to `PUT /v1/devices/me/push-token` (`development` in Debug, `production` in Release).
- Categories: `VOX_APPROVAL_LOW` [Aprobar (first, so Double Tap approves), Rechazar] and `VOX_APPROVAL_HIGH` [Revisar, opens the app]. Aprobar/Rechazar run in the background and make exactly one signed call; on failure the watch posts a local "No se pudo aprobar, toca para reintentar" notification.

## Enable Whistle (experimental, on-device ASR)

1. Run `scripts/fetch-needle.sh`. It checks the model size (16,919,407 bytes) and prints sha256 values. Pin those values in the script (there is a TODO) after the first verified download.
2. Build with the `Debug-Whistle` or `Release-Whistle` configuration. These configs:
   - define `VOX_WHISTLE`
   - link `libneedle.a` with `-lc++`
   - raise the deployment target to watchOS 26.2
   - embed `whistle.cact` in the app bundle
3. They need a physical watch running watchOS 26.2 or later. `libneedle.a` has no simulator slice, so the simulator cannot link it.

Without `VOX_WHISTLE`, `WhistleTranscriber` is a stub. It reports `blocked` and never returns text, so the app falls back to system dictation (a `TextField`, sent as engine `system_dictation`). Remote audio ASR exists only as a consent flag (default OFF) and a `RemoteTranscriber` stub that throws `notImplemented`.

## TestFlight

Push a `v*` tag (e.g. `v0.2.0`, which becomes the version) or run the `watch-testflight` workflow by hand. It runs `bundle exec fastlane ios beta` from `apps/watch`: registers missing App IDs, signs with fastlane match (encrypted in musiquillas' private `certificates` branch, reached with `MATCH_DEPLOY_KEY`), archives the `VOX` scheme and uploads an internal TestFlight build numbered with the UTC timestamp.

Bundle ids: `com.santilega.vox` (the iOS stub Xcode requires for a watch-only app; this is the App Store Connect app), `com.santilega.vox.watchkitapp` (the watch app) and `com.santilega.vox.watchkitapp.widgets`. The App Store Connect API cannot create app records, so the `com.santilega.vox` app must be added once by hand in App Store Connect.

## Privacy

- The device token and `device_id` are stored in the Keychain only (`AfterFirstUnlockThisDeviceOnly`); the signing key is `WhenPasscodeSetThisDeviceOnly` and never leaves the watch.
- The outbox is a JSON file in Application Support, written atomically with `completeUntilFirstUserAuthentication`.
- Logs contain ids, statuses and error codes only, never transcripts, agent summaries or tokens.
- Audio stays in memory and is dropped once it has been transcribed.

## Validation status

- **NOT compiled.** These files were written on Windows with no macOS, Xcode or Swift toolchain. Neither `swift test` nor an Xcode build has been run yet. This includes the v0.4 pairing, signing and push code.
- Push, notification actions and Double Tap have not been tried on a device.
- **Whistle hardware validation is BLOCKED.** It has not run on a device. These needle details are unverified:
  - whether `needle_load` copies or references the model buffer
  - how `needle_transcribe` reports its output size
  - the exact Hugging Face file paths
- `AVAudioEngine` capture and resampling on a real watch have not been tested.
