// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ReaderCore",
    platforms: [
        .iOS(.v18),
        .macOS(.v14),
    ],
    products: [
        .library(name: "ReaderCore", targets: ["ReaderCore"]),
    ],
    targets: [
        .target(name: "ReaderCore"),
        .testTarget(
            name: "ReaderCoreTests",
            dependencies: ["ReaderCore"],
            resources: [.copy("Fixtures")]
        ),
    ],
    swiftLanguageModes: [.v6]
)
