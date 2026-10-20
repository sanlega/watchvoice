# VOX Agent Watch (watchOS)

An independent watchOS app (no iOS companion). You speak or dictate an instruction, review the transcript, and send it to `POST /v1/instructions` (contract: `contracts/openapi.yaml` v0.3). The app then follows routing and T3 Code run progress by polling `GET /v1/instructions/{id}` while it is in the foreground.

## Layout

- `VoxWatchCore/`: a Swift package with all non-UI logic (models, API client, Keychain, outbox, delivery, state machine, ASR selection, Whistle wrapper). The Xcode project also builds these same sources as a watchOS framework.
- `VoxWatch/`: the SwiftUI app (views, `AppModel`, `AVAudioEngineCapture`).
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
- delivery: retries reuse the same Idempotency-Key, 409/413/422 fail permanently, 429 honours Retry-After, an offline outbox delivers once back online, 401 halts the queue
- the Whistle stub, the ASR selector and `APIError` retriability

## Enable Whistle (experimental, on-device ASR)

1. Run `scripts/fetch-needle.sh`. It checks the model size (16,919,407 bytes) and prints sha256 values. Pin those values in the script (there is a TODO) after the first verified download.
2. Build with the `Debug-Whistle` or `Release-Whistle` configuration. These configs:
   - define `VOX_WHISTLE`
   - link `libneedle.a` with `-lc++`
   - raise the deployment target to watchOS 26.2
   - embed `whistle.cact` in the app bundle
3. They need a physical watch running watchOS 26.2 or later. `libneedle.a` has no simulator slice, so the simulator cannot link it.

Without `VOX_WHISTLE`, `WhistleTranscriber` is a stub. It reports `blocked` and never returns text, so the app falls back to system dictation (a `TextField`, sent as engine `system_dictation`). Remote audio ASR exists only as a consent flag (default OFF) and a `RemoteTranscriber` stub that throws `notImplemented`.

## Privacy

- The device token is stored in the Keychain only (`AfterFirstUnlockThisDeviceOnly`).
- The outbox is a JSON file in Application Support, written atomically with `completeUntilFirstUserAuthentication`.
- Logs contain ids, statuses and error codes only, never transcripts, agent summaries or tokens.
- Audio stays in memory and is dropped once it has been transcribed.

## Validation status

- **NOT compiled.** These files were written on Windows with no macOS, Xcode or Swift toolchain. Neither `swift test` nor an Xcode build has been run yet.
- **Whistle hardware validation is BLOCKED.** It has not run on a device. These needle details are unverified:
  - whether `needle_load` copies or references the model buffer
  - how `needle_transcribe` reports its output size
  - the exact Hugging Face file paths
- `AVAudioEngine` capture and resampling on a real watch have not been tested.
