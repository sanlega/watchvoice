import AVFoundation
import VoxWatchCore

enum CaptureError: Error {
    case permissionDenied
    case converterUnavailable
}

/// Mic capture converted to 16 kHz mono Float32. Conversion runs on the audio tap thread, never the main actor.
/// Recording only starts from an explicit user tap (AppModel.speakTapped).
final class AVAudioEngineCapture: AudioCapture, @unchecked Sendable {
    // @unchecked: `samples` is only touched under `lock`; engine/converter are only mutated by
    // start/stop/cancel, which AppModel calls sequentially from the main actor.
    private let lock = NSLock()
    private let maxSamples: Int
    private let target: AVAudioFormat
    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private var samples: [Float] = []

    init(maxDuration: TimeInterval) {
        maxSamples = Int(maxDuration * CapturedAudio.sampleRate)
        // A standard deinterleaved Float32 format; this initializer cannot fail for these values.
        target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: CapturedAudio.sampleRate, channels: 1, interleaved: false)!
    }

    func start() async throws {
        let granted = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard granted else { throw CaptureError.permissionDenied }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .default, options: [])
        try session.setActive(true)

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: inputFormat, to: target) else {
            throw CaptureError.converterUnavailable
        }
        resetSamples()
        self.converter = converter
        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.append(buffer)
        }
        engine.prepare()
        try engine.start()
        self.engine = engine
    }

    func stop() async throws -> CapturedAudio {
        teardown()
        return CapturedAudio(samples: takeSamples())
    }

    func cancel() async {
        teardown()
        resetSamples()
    }

    private func teardown() {
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        converter = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    /// Audio thread.
    private func append(_ buffer: AVAudioPCMBuffer) {
        guard let converter, buffer.format.sampleRate > 0 else { return }
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }

        nonisolated(unsafe) let input = buffer
        nonisolated(unsafe) var supplied = false
        var error: NSError?
        // `.noDataNow` (not end-of-stream) keeps the resampler state across tap buffers.
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            if supplied {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return input
        }
        guard status != .error, error == nil, let channel = output.floatChannelData?[0] else { return }
        appendSamples(UnsafeBufferPointer(start: channel, count: Int(output.frameLength)))
    }

    private func appendSamples(_ chunk: UnsafeBufferPointer<Float>) {
        lock.lock()
        defer { lock.unlock() }
        let room = maxSamples - samples.count
        if room > 0 { samples.append(contentsOf: chunk.prefix(room)) }
    }

    private func takeSamples() -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        let captured = samples
        samples = []
        return captured
    }

    private func resetSamples() {
        lock.lock()
        defer { lock.unlock() }
        samples = []
        samples.reserveCapacity(maxSamples)
    }
}
