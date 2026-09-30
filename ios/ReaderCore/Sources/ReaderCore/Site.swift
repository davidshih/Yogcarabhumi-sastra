import Foundation

public enum ReaderError: Error, Equatable, Sendable {
    case noImageList
    case http(status: Int, url: URL)
    case libraryLocked
    case notAnImage(url: URL)
}

public enum Site {
    public static let base = URL(string: "https://www.manhuaren.com")!
    public static let userAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    public static let referer = "https://www.manhuaren.com/"
    /// The site applies rights-holder takedowns (已下架) only to Chinese-locale requests: an en-US
    /// Accept-Language gets full chapter lists for removed series. Pin zh-CN on every request so the app
    /// sees the same site as tracker.py and never reads removed series back in, whatever the phone's language.
    public static let acceptLanguage = "zh-CN,zh;q=0.9"
    public static let pageHeaders = ["User-Agent": userAgent, "Accept-Language": acceptLanguage]
    public static let imageHeaders = pageHeaders.merging(["Referer": referer]) { _, new in new }

    public static func seriesURL(slug: String) -> URL {
        base.appendingPathComponent("manhua-\(slug)").appendingPathComponent("")
    }

    public static func chapterURL(id: String) -> URL {
        base.appendingPathComponent(id).appendingPathComponent("")
    }

    public static func slug(from input: String) -> String? {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }

        if value.contains("/") || value.contains(".") {
            let candidate = value.contains("://") ? value : "https://\(value)"
            guard let components = URLComponents(string: candidate),
                  let host = components.host?.lowercased(),
                  ["manhuaren.com", "www.manhuaren.com", "m.manhuaren.com"].contains(host),
                  let match = firstMatch(#"^/manhua-([^/]+)/?$"#, in: components.path),
                  match.count == 2 else { return nil }
            return match[1]
        }

        guard firstMatch(#"^[A-Za-z0-9-]+$"#, in: value) != nil,
              firstMatch(#"^m\d+$"#, in: value) == nil else { return nil }
        return value
    }
}

public struct SeriesPage: Equatable, Sendable {
    public var name: String
    public var status: String
    public var latestUpdate: String
    public var coverURL: URL?
    public var chapters: [Chapter]

    public init(name: String, status: String, latestUpdate: String, coverURL: URL?, chapters: [Chapter]) {
        self.name = name
        self.status = status
        self.latestUpdate = latestUpdate
        self.coverURL = coverURL
        self.chapters = chapters
    }
}

public enum SiteParser {
    public static func parseSeries(_ html: String, now: Date) -> SeriesPage {
        let name = firstMatch(#"<title>(.+?)漫画_"#, in: html, options: [.dotMatchesLineSeparators])?[1]
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let status = classText("detail-list-title-1", in: html)
        let latestUpdate = absoluteDate(classText("detail-list-title-3", in: html), now: now)

        var chapters: [Chapter] = []
        if let list = firstMatch(#"id=[\"']detail-list-select-1[\"'][^>]*>(.*?)</ul>"#, in: html, options: [.dotMatchesLineSeparators])?[1] {
            chapters = allMatches(#"href=[\"']/(m\d+)/[\"'][^>]*>([^<]+)<"#, in: list).compactMap { groups in
                guard groups.count == 3 else { return nil }
                return Chapter(id: groups[1], label: groups[2].trimmingCharacters(in: .whitespacesAndNewlines))
            }
        }
        let volume = try! NSRegularExpression(pattern: #"^第\s*\d+\s*卷$"#)
        func isVolume(_ chapter: Chapter) -> Bool {
            volume.firstMatch(in: chapter.label, range: NSRange(chapter.label.startIndex..., in: chapter.label)) != nil
        }
        if chapters.contains(where: { !isVolume($0) }) {
            chapters.removeAll(where: isVolume)
        }

        var coverURL: URL?
        if let cover = firstMatch(#"class=[\"'][^\"']*detail-main-cover[^\"']*[\"'][^>]*>(.*?)</div>"#, in: html, options: [.dotMatchesLineSeparators])?[1],
           let source = firstMatch(#"<img[^>]*src=[\"']([^\"']+)[\"']"#, in: cover)?[1] {
            coverURL = URL(string: decodeEntities(source))
        }
        return SeriesPage(name: name, status: status, latestUpdate: latestUpdate, coverURL: coverURL, chapters: chapters)
    }

    public static func imageURLs(_ chapterHTML: String) throws -> [URL] {
        let pattern = #"\}\('((?:\\.|[^'])*)',(\d+),(\d+),'((?:\\.|[^'])*)'\.split\('\|'\),0,\{\}\)\)"#
        guard let packed = firstMatch(pattern, in: chapterHTML, options: [.dotMatchesLineSeparators]), packed.count == 5,
              let radix = Int(packed[2]), let count = Int(packed[3]), radix >= 2, radix <= 62 else {
            throw ReaderError.noImageList
        }

        var payload = packed[1]
            .replacingOccurrences(of: #"\'"#, with: "'")
            .replacingOccurrences(of: #"\\"#, with: #"\"#)
        let words = packed[4].split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        for index in stride(from: count - 1, through: 0, by: -1) where index < words.count && !words[index].isEmpty {
            payload = payload.replacingOccurrences(
                of: #"\b"# + NSRegularExpression.escapedPattern(for: packerKey(index, radix: radix)) + #"\b"#,
                with: words[index],
                options: .regularExpression
            )
        }

        guard let array = firstMatch(#"newImgs\s*=\s*\[(.*?)\]"#, in: payload, options: [.dotMatchesLineSeparators])?[1] else {
            throw ReaderError.noImageList
        }
        let urls = allMatches(#"[\"']([^\"']+)[\"']"#, in: array).compactMap { match -> URL? in
            guard match.count == 2 else { return nil }
            return URL(string: decodeEntities(match[1]))
        }
        guard !urls.isEmpty else { throw ReaderError.noImageList }
        return urls
    }
}

private func absoluteDate(_ text: String, now: Date) -> String {
    for (prefix, days) in [("今天", 0), ("昨天", 1), ("前天", 2)] where text.hasPrefix(prefix) {
        let date = Calendar.current.date(byAdding: .day, value: -days, to: now) ?? now
        let components = Calendar.current.dateComponents([.month, .day], from: date)
        return String(format: "%02d月%02d号", components.month ?? 0, components.day ?? 0)
    }
    return text
}

private func classText(_ name: String, in html: String) -> String {
    let escaped = NSRegularExpression.escapedPattern(for: name)
    let pattern = #"class=[\"'][^\"']*\b"# + escaped + #"\b[^\"']*[\"'][^>]*>([^<]*)<"#
    return firstMatch(pattern, in: html)?[1].trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
}

private func packerKey(_ value: Int, radix: Int) -> String {
    let digits = Array("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if value < radix { return String(digits[value]) }
    return packerKey(value / radix, radix: radix) + String(digits[value % radix])
}

private func decodeEntities(_ value: String) -> String {
    value.replacingOccurrences(of: "&amp;", with: "&")
}

private func firstMatch(
    _ pattern: String,
    in text: String,
    options: NSRegularExpression.Options = []
) -> [String]? {
    allMatches(pattern, in: text, options: options).first
}

private func allMatches(
    _ pattern: String,
    in text: String,
    options: NSRegularExpression.Options = []
) -> [[String]] {
    guard let expression = try? NSRegularExpression(pattern: pattern, options: options) else { return [] }
    let range = NSRange(text.startIndex..., in: text)
    return expression.matches(in: text, range: range).map { match in
        (0..<match.numberOfRanges).map { index in
            let range = match.range(at: index)
            guard range.location != NSNotFound, let swiftRange = Range(range, in: text) else { return "" }
            return String(text[swiftRange])
        }
    }
}
