// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "vibe-bar-i18n",
    // The catalog is shared with a cross-platform client, but the Swift
    // lane exists for one consumer: the macOS menu-bar app, which is
    // macOS 26 only.
    defaultLocalization: "en",
    platforms: [
        .macOS(.v26)
    ],
    products: [
        .library(name: "VibeBarLocalization", targets: ["VibeBarLocalization"])
    ],
    // Swift and TypeScript are peer implementation lanes under
    // `implementations/`. Both manifests sit at the repository root and
    // point inward with explicit paths, so a consumer's dependency URL and
    // `import VibeBarLocalization` never mention a lane — the same shape
    // AstroQore/agent-session-kit uses.
    targets: [
        // `path` is the lane root rather than the sources directory because
        // SwiftPM resolves `resources:` relative to `path` and refuses to
        // look outside it; `sources:` then narrows compilation back to the
        // one source directory.
        //
        // `.process`, not `.copy` — and this was measured, not assumed.
        // All three plausible spellings were built and tested here on
        // Swift 6.3 / macOS 26; the bundles they produce differ:
        //
        //   .process("Resources")        -> en.lproj/, zh-hans.lproj/ at
        //                                   the bundle root
        //   .copy("Resources")           -> Resources/en.lproj/…
        //   .process("../../Resources")  -> Resources/en.lproj/…
        //     (with path: "…/Sources/VibeBarLocalization")
        //
        // Only the first is a localization SwiftPM understands: it reads
        // the `.lproj` directories, registers them against
        // `defaultLocalization`, and flattens them to the bundle root —
        // the layout Xcode produces. The other two copy the tree verbatim
        // one level too deep. They *happen* to resolve anyway on macOS,
        // because a bundle with `Info.plist` at its root and a
        // `Resources/` directory is a CFBundle "version 1" bundle and
        // CFBundle scans that directory for `.lproj`. That is a
        // coincidence of the bundle shape, not a contract — precisely the
        // kind of thing that holds under `swift test` and stops holding
        // somewhere else. Take the rule that is right by construction.
        //
        // One consequence to know about: SwiftPM lowercases the directory
        // it emits, so `zh-Hans` in `catalog/` becomes `zh-hans.lproj` in
        // the built bundle. `L10n.availableLocales` therefore reports the
        // catalog's canonical tags and the bundle lookup tries both
        // spellings; `implementations/swift/Tests` asserts all of it.
        .target(
            name: "VibeBarLocalization",
            path: "implementations/swift",
            exclude: ["Tests"],
            sources: ["Sources/VibeBarLocalization"],
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "VibeBarLocalizationTests",
            dependencies: ["VibeBarLocalization"],
            path: "implementations/swift/Tests/VibeBarLocalizationTests"
        )
    ]
)
