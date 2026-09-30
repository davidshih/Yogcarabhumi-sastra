import Foundation

public struct Chapter: Codable, Hashable, Sendable {
    public let id: String
    public let label: String

    public init(id: String, label: String) {
        self.id = id
        self.label = label
    }
}

public enum ReadingMode: String, Codable, Sendable, CaseIterable {
    case vertical
    case pagedRTL
}

public enum ProgressState: Equatable, Sendable {
    case new(unread: Int, next: Chapter)
    case caught(gone: Bool)
    case unknown(missing: Bool)
}

public struct Series: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
    public var chapters: [Chapter]
    public var lastRead: Chapter?
    public var status: String
    public var latestUpdate: String
    public var coverURL: URL?
    public var readAt: Date?
    public var views: Int
    public var readingMode: ReadingMode
    public var resumePage: [String: Int]
    public var checkedAt: Date?
    /// Starred by the user; listed first.
    public var isFavorite: Bool
    /// Chapters the user downloaded on purpose; sync never evicts them.
    public var pinnedChapters: Set<String>

    public init(
        id: String,
        name: String,
        chapters: [Chapter] = [],
        lastRead: Chapter? = nil,
        status: String = "",
        latestUpdate: String = "",
        coverURL: URL? = nil,
        readAt: Date? = nil,
        views: Int = 0,
        readingMode: ReadingMode = .vertical,
        resumePage: [String: Int] = [:],
        checkedAt: Date? = nil,
        isFavorite: Bool = false,
        pinnedChapters: Set<String> = []
    ) {
        self.id = id
        self.name = name
        self.chapters = chapters
        self.lastRead = lastRead
        self.status = status
        self.latestUpdate = latestUpdate
        self.coverURL = coverURL
        self.readAt = readAt
        self.views = views
        self.readingMode = readingMode
        self.resumePage = resumePage
        self.checkedAt = checkedAt
        self.isFavorite = isFavorite
        self.pinnedChapters = pinnedChapters
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, chapters, lastRead, status, latestUpdate, coverURL, readAt, views, readingMode, resumePage, checkedAt
        case isFavorite, pinnedChapters
    }

    /// Fields added after the first release are optional, so a library.json written by an older build still loads.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        chapters = try c.decode([Chapter].self, forKey: .chapters)
        lastRead = try c.decodeIfPresent(Chapter.self, forKey: .lastRead)
        status = try c.decode(String.self, forKey: .status)
        latestUpdate = try c.decode(String.self, forKey: .latestUpdate)
        coverURL = try c.decodeIfPresent(URL.self, forKey: .coverURL)
        readAt = try c.decodeIfPresent(Date.self, forKey: .readAt)
        views = try c.decode(Int.self, forKey: .views)
        readingMode = try c.decode(ReadingMode.self, forKey: .readingMode)
        resumePage = try c.decode([String: Int].self, forKey: .resumePage)
        checkedAt = try c.decodeIfPresent(Date.self, forKey: .checkedAt)
        isFavorite = try c.decodeIfPresent(Bool.self, forKey: .isFavorite) ?? false
        pinnedChapters = try c.decodeIfPresent(Set<String>.self, forKey: .pinnedChapters) ?? []
    }

    public var progress: ProgressState {
        guard !chapters.isEmpty else { return .caught(gone: true) }
        guard let lastRead else { return .unknown(missing: false) }
        guard let index = chapters.firstIndex(where: { $0.id == lastRead.id }) else {
            return .unknown(missing: true)
        }
        if index == 0 { return .caught(gone: false) }
        return .new(unread: index, next: chapters[index - 1])
    }

    public func prefetchCandidates(limit: Int) -> [Chapter] {
        guard limit > 0, case .new(let unread, _) = progress else { return [] }
        return Array(chapters[max(0, unread - limit)..<unread].reversed())
    }

    public func evictable(cached: Set<String>) -> Set<String> {
        guard let lastRead,
              let index = chapters.firstIndex(where: { $0.id == lastRead.id }),
              index + 1 < chapters.count else { return [] }
        return Set(chapters[(index + 1)...].map(\.id)).intersection(cached)
            .subtracting(pinnedChapters)
            .subtracting(latestChapters.map(\.id))
    }

    /// How many of the newest chapters every series keeps on the phone, read or not.
    public static let latestKept = 2

    /// The newest chapters, which stay downloaded so the latest release is always ready.
    public var latestChapters: [Chapter] { Array(chapters.prefix(Self.latestKept)) }

    public mutating func markFinished(_ chapter: Chapter, at date: Date) {
        guard let targetIndex = chapters.firstIndex(where: { $0.id == chapter.id }) else { return }
        if let lastRead,
           let currentIndex = chapters.firstIndex(where: { $0.id == lastRead.id }),
           targetIndex >= currentIndex {
            return
        }
        self.lastRead = chapters[targetIndex]
        readAt = date
    }

    public mutating func setLastRead(_ chapter: Chapter?, at date: Date) {
        lastRead = chapter
        readAt = date
    }
}
