// The only hand-written file in this target. Everything under `Generated/`
// is produced by `scripts/generate.py` and must not be edited; this file is
// the small runtime that the generated code calls into.
//
// Responsibilities, and nothing else:
//   * hold the explicit interface-language override,
//   * turn that override (or the system language) into a `Bundle`,
//   * run the two `String(format:)` shapes the generator emits.

import Foundation

// MARK: - Public surface

extension L10n {
    /// An explicit interface language, or `nil` to follow the system.
    ///
    /// Set it to a BCP 47 tag that this package ships — see
    /// ``L10n/availableLocales``. The value is matched leniently: `"zh"`,
    /// `"zh-Hans"` and `"zh-Hans-CN"` all resolve to the `zh-Hans.lproj`
    /// bundle, and an identifier that matches nothing falls back to the
    /// system language rather than to raw keys.
    ///
    /// ```swift
    /// L10n.localeOverride = "zh-Hans"   // force Simplified Chinese
    /// L10n.localeOverride = nil         // back to the macOS language
    /// ```
    ///
    /// Every generated accessor is a computed property or a function, so a
    /// change here is visible on the next read — there is no cached string
    /// to invalidate. SwiftUI views still need their own invalidation: the
    /// usual shape is to store the choice in `@AppStorage`, write it
    /// through to this property, and key the root view on it.
    public static var localeOverride: String? {
        get { state.localeOverride }
        set { state.localeOverride = newValue }
    }

    /// The bundle the generated accessors read from.
    ///
    /// With no override this is the package's own resource bundle, so
    /// lookup follows the host application's preferred localizations —
    /// the standard macOS behaviour. With an override it is the matching
    /// `<locale>.lproj` sub-bundle.
    public static var bundle: Bundle { state.resolvedBundle }

    /// The BCP 47 tags this package ships, sorted.
    public static var availableLocales: [String] { state.availableLocales }

    /// The locale the next lookup will actually use, after matching the
    /// override (or the system's preference) against what ships here.
    public static var resolvedLocale: String { state.resolvedLocale }
}

// MARK: - The runtime the generated code calls

/// Not `public`: call sites use ``L10n``, never this.
enum L10nSupport {
    /// A key with no values in it.
    static func string(_ key: String) -> String {
        state.resolvedBundle.localizedString(forKey: key, value: nil, table: nil)
    }

    /// A key with values but no plural.
    ///
    /// Formatted with a `nil` locale on purpose: the catalog's contract is
    /// that the caller has already formatted anything that needs a number
    /// formatter (AGENTS.md § 3), so a locale here would only add
    /// unrequested digit grouping to a raw `Int`.
    static func format(_ key: String, _ arguments: CVarArg...) -> String {
        String(format: string(key), arguments: arguments)
    }

    /// A key whose value contains an ICU plural, resolved through
    /// `Localizable.stringsdict`.
    ///
    /// This one *must* pass a locale: the `%#@name@` variable is expanded
    /// by the locale-aware formatter, and the plural category comes from
    /// that locale's CLDR rules.
    static func localizedFormat(_ key: String, _ arguments: CVarArg...) -> String {
        String(
            format: string(key),
            locale: Locale(identifier: state.resolvedLocale),
            arguments: arguments
        )
    }
}

// MARK: - State

private let state = L10nState()

/// Locked mutable state. `@unchecked Sendable` because the lock, not the
/// type system, is what makes the stored properties safe to touch from any
/// thread — a menu-bar app changes the language from the UI thread while a
/// refresh actor is formatting a status line.
private final class L10nState: @unchecked Sendable {
    private let lock = NSLock()
    private var override: String?
    private var cachedBundle: Bundle?
    private var cachedLocale: String?

    init() {
        // The system-language path is resolved once and cached, so the
        // resolution has to be dropped when the user changes their language
        // preferences while the app is running.
        NotificationCenter.default.addObserver(
            forName: NSLocale.currentLocaleDidChangeNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.invalidate()
        }
    }

    private func invalidate() {
        lock.withLock {
            cachedBundle = nil
            cachedLocale = nil
        }
    }

    var localeOverride: String? {
        get { lock.withLock { override } }
        set {
            lock.withLock {
                override = newValue
                cachedBundle = nil
                cachedLocale = nil
            }
        }
    }

    var resolvedBundle: Bundle {
        lock.withLock {
            if let cachedBundle { return cachedBundle }
            let resolved = Self.resolve(override: override)
            cachedBundle = resolved.bundle
            cachedLocale = resolved.locale
            return resolved.bundle
        }
    }

    var resolvedLocale: String {
        lock.withLock {
            if let cachedLocale { return cachedLocale }
            let resolved = Self.resolve(override: override)
            cachedBundle = resolved.bundle
            cachedLocale = resolved.locale
            return resolved.locale
        }
    }

    var availableLocales: [String] { Self.shipped }

    // MARK: Resolution

    /// The locales this package ships, in their canonical BCP 47 spelling.
    ///
    /// Taken from the generated catalog facts rather than from
    /// `Bundle.module.localizations`, because SwiftPM lowercases the
    /// `.lproj` directory it emits (`zh-Hans` in `catalog/` becomes
    /// `zh-hans.lproj` in the built bundle). A language picker should show
    /// the tag the catalog authored, not the one the build system spelled.
    private static let shipped: [String] = L10nCatalogFacts.locales

    /// Resolve the override, or the user's language preferences, to one of
    /// the locales this package ships.
    ///
    /// The system path deliberately matches `Locale.preferredLanguages`
    /// against ``shipped`` by hand instead of asking
    /// `Bundle.module.preferredLocalizations`. That property was measured
    /// returning `["en"]` on a machine whose language list began with
    /// `zh-Hans`, because CFBundle constrains a sub-bundle's language
    /// search list to the *main* bundle's `CFBundleLocalizations` — a host
    /// that has not declared `zh-Hans` in its `Info.plist` silently pins
    /// this package to its development region. That is the wrong default
    /// for a package whose entire job is the strings: if the catalog ships
    /// a language and the user asked for it, show it. Hosts should still
    /// list their localizations in `Info.plist`, for the system's own
    /// per-app language picker.
    private static func resolve(override: String?) -> (bundle: Bundle, locale: String) {
        if let override, let match = match(override) {
            return (match.bundle, match.locale)
        }
        for preferred in Locale.preferredLanguages {
            if let match = match(preferred) { return match }
        }
        if let fallback = match(L10nCatalogFacts.sourceLocale) { return fallback }
        return (.module, L10nCatalogFacts.sourceLocale)
    }

    /// Match an identifier against the shipped locales, longest tag first:
    /// `zh-Hans-CN` → `zh-Hans` → `zh`, then any shipped locale in the same
    /// language (so a bare `zh` still finds `zh-Hans`).
    private static func match(_ identifier: String) -> (bundle: Bundle, locale: String)? {
        for candidate in candidates(for: identifier) {
            guard let bundle = lproj(candidate) else { continue }
            return (bundle, candidate)
        }
        return nil
    }

    /// The shipped tag a bundle-supplied identifier corresponds to.
    private static func canonical(_ identifier: String) -> String? {
        shipped.first { $0.caseInsensitiveCompare(identifier) == .orderedSame }
    }

    /// The `.lproj` sub-bundle for a canonical tag.
    ///
    /// Two spellings are tried because the directory SwiftPM writes is
    /// lowercased; the exact spelling still resolves on a case-insensitive
    /// volume, and the lowercased one keeps this working on a case-sensitive
    /// one.
    private static func lproj(_ tag: String) -> Bundle? {
        for spelling in [tag, tag.lowercased()] {
            if let path = Bundle.module.path(forResource: spelling, ofType: "lproj"),
               let bundle = Bundle(path: path) {
                return bundle
            }
        }
        return nil
    }

    private static func candidates(for identifier: String) -> [String] {
        var segments = identifier
            .replacingOccurrences(of: "_", with: "-")
            .split(separator: "-")
            .map(String.init)
        var result: [String] = []
        while !segments.isEmpty {
            if let tag = canonical(segments.joined(separator: "-")), !result.contains(tag) {
                result.append(tag)
            }
            segments.removeLast()
        }
        if let language = identifier.split(separator: "-").first.map(String.init) {
            let prefix = language.lowercased() + "-"
            for locale in shipped
            where locale.lowercased().hasPrefix(prefix) && !result.contains(locale) {
                result.append(locale)
            }
        }
        return result
    }
}
