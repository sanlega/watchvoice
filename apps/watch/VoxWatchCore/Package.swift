// swift-tools-version: 6.0
import PackageDescription

// Non-UI logic for the VOX watch app. macOS is listed only so `swift test` runs on a Mac.
// The Xcode project (project.yml) compiles these same sources as a watchOS framework.
let package = Package(
    name: "VoxWatchCore",
    platforms: [.watchOS(.v10), .macOS(.v14)],
    products: [.library(name: "VoxWatchCore", targets: ["VoxWatchCore"])],
    targets: [
        .target(name: "VoxWatchCore"),
        .testTarget(
            name: "VoxWatchCoreTests",
            dependencies: ["VoxWatchCore"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
