import XCTest
@testable import VoxWatchCore

final class APIErrorTests: XCTestCase {
    func testRetriabilityTable() {
        let table: [(APIError, Bool)] = [
            (.offline("x"), true),
            (.server(status: 503), true),
            (.rateLimited(retryAfter: 5), true),
            (.unauthorized, false),
            (.forbidden, false),
            (.notFound, false),
            (.conflict(code: nil), false),
            (.invalid(status: 413, code: nil), false),
            (.invalid(status: 422, code: nil), false),
            (.decoding("x"), false),
            (.insecureBaseURL, false),
        ]
        for (error, retriable) in table {
            XCTAssertEqual(error.isRetriable, retriable, "\(error)")
        }
    }

    func testHTTPStatusMapping() {
        XCTAssertNil(APIError.from(status: 202, retryAfter: nil, problemCode: nil))
        XCTAssertEqual(APIError.from(status: 401, retryAfter: nil, problemCode: nil), .unauthorized)
        XCTAssertEqual(APIError.from(status: 403, retryAfter: nil, problemCode: nil), .forbidden)
        XCTAssertEqual(APIError.from(status: 404, retryAfter: nil, problemCode: nil), .notFound)
        XCTAssertEqual(APIError.from(status: 409, retryAfter: nil, problemCode: "idempotency_conflict"), .conflict(code: "idempotency_conflict"))
        XCTAssertEqual(APIError.from(status: 413, retryAfter: nil, problemCode: nil), .invalid(status: 413, code: nil))
        XCTAssertEqual(APIError.from(status: 422, retryAfter: nil, problemCode: nil), .invalid(status: 422, code: nil))
        XCTAssertEqual(APIError.from(status: 429, retryAfter: "30", problemCode: nil), .rateLimited(retryAfter: 30))
        XCTAssertEqual(APIError.from(status: 429, retryAfter: "Wed, 21 Oct 2026 07:28:00 GMT", problemCode: nil), .rateLimited(retryAfter: nil))
        XCTAssertEqual(APIError.from(status: 502, retryAfter: nil, problemCode: nil), .server(status: 502))
    }

    func testBaseURLMustBeHTTPSExceptLocalhost() throws {
        XCTAssertTrue(URLSessionWatchAPIClient.isAllowed(try XCTUnwrap(URL(string: "https://vox.example.com/v1"))))
        XCTAssertTrue(URLSessionWatchAPIClient.isAllowed(try XCTUnwrap(URL(string: "http://localhost:8000/v1"))))
        XCTAssertTrue(URLSessionWatchAPIClient.isAllowed(try XCTUnwrap(URL(string: "http://127.0.0.1:8000/v1"))))
        XCTAssertFalse(URLSessionWatchAPIClient.isAllowed(try XCTUnwrap(URL(string: "http://vox.example.com/v1"))))
        XCTAssertFalse(URLSessionWatchAPIClient.isAllowed(try XCTUnwrap(URL(string: "ftp://vox.example.com"))))
    }
}

#if !VOX_WHISTLE
final class WhistleStubTests: XCTestCase {
    func testStubReportsBlockedAndNeverReturnsText() async {
        let whistle = WhistleTranscriber.shared
        let availability = await whistle.availability()
        guard case .blocked(let reason) = availability else {
            return XCTFail("stub must report blocked")
        }
        XCTAssertTrue(reason.contains("libneedle.a"))

        do {
            _ = try await whistle.transcribe(CapturedAudio(samples: [0, 0.1, -0.1]), language: "es", keywords: [])
            XCTFail("stub must never return text")
        } catch {
            XCTAssertEqual(error as? ASRError, .blocked(reason))
        }
    }

    func testSelectorFallsBackToSystemDictation() async {
        let selector = TranscriberSelector(whistle: WhistleTranscriber.shared, remote: RemoteTranscriber(), mock: MockTranscriber())
        var route = await selector.select(.init())
        XCTAssertEqual(route.engine, .systemDictation)

        // Consent alone does not enable remote ASR while it is not implemented.
        route = await selector.select(.init(remoteAudioConsent: true))
        XCTAssertEqual(route.engine, .systemDictation)

        #if DEBUG
        route = await selector.select(.init(useMock: true))
        XCTAssertEqual(route.engine, .mock)
        #endif
    }

    func testRemoteTranscriberIsNotImplemented() async {
        do {
            _ = try await RemoteTranscriber().transcribe(CapturedAudio(samples: [0]), language: nil, keywords: [])
            XCTFail("remote must not return text")
        } catch {
            XCTAssertEqual(error as? ASRError, .notImplemented)
        }
    }
}
#endif
