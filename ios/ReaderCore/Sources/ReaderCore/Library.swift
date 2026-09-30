import Foundation

public struct SyncReport: Equatable, Sendable {
    public var refreshed: [String] = []
    public var failed: [String] = []
    public var downloaded: [String] = []
    public var evicted: [String] = []

    public init() {}
}

public actor Library {
    private let store: LibraryStore
    private let cache: ChapterCache
    private let site: SiteClient
    private let downloader: ChapterDownloader
    private let prefetchLimit: Int
    private var items: [Series] = []
    private var onChange: (@Sendable ([Series]) -> Void)?
    private var inFlight: [String: Task<[URL], Error>] = [:]
    private var isSyncing = false

    public private(set) var canSave = false

    public init(
        store: LibraryStore,
        cache: ChapterCache,
        site: SiteClient,
        downloader: ChapterDownloader,
        prefetchLimit: Int = 3
    ) {
        self.store = store
        self.cache = cache
        self.site = site
        self.downloader = downloader
        self.prefetchLimit = prefetchLimit
    }

    public var series: [Series] { items }

    public func load() throws {
        let loaded = try store.load()
        items = loaded
        canSave = true
    }

    public func setOnChange(_ handler: @escaping @Sendable ([Series]) -> Void) {
        onChange = handler
    }

    @discardableResult
    public func importSeed(_ data: Data) throws -> Int {
        try requireLoad()
        let imported = try SeedImporter.decode(data)
        let candidate = SeedImporter.merge(existing: items, imported: imported)
        let added = candidate.count - items.count
        if added > 0 { try commit(candidate) }
        return added
    }

    public func update(_ id: String, _ change: @Sendable (inout Series) -> Void) throws {
        try requireLoad()
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        var candidate = items
        change(&candidate[index])
        try commit(candidate)
    }

    public func remove(_ id: String) throws {
        try requireLoad()
        guard let removed = items.first(where: { $0.id == id }) else { return }
        let candidate = items.filter { $0.id != id }
        try commit(candidate)
        for chapter in removed.chapters {
            inFlight[chapter.id]?.cancel()
            try cache.remove(chapter.id)
        }
    }

    @discardableResult
    public func add(_ series: Series) throws -> Bool {
        try requireLoad()
        guard !items.contains(where: { $0.id == series.id }) else { return false }
        try commit(items + [series])
        return true
    }

    public func refresh(_ id: String, now: Date = Date()) async throws {
        try requireLoad()
        guard let current = items.first(where: { $0.id == id }) else { return }
        let siteVersion = try await site.refresh(current, now: now)
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        var candidate = items
        candidate[index].applySiteFields(from: siteVersion)
        try commit(candidate)
    }

    public func cachedChapterIDs() -> Set<String> {
        cache.cachedChapterIDs()
    }

    public func removeCachedChapter(_ chapterID: String) async throws {
        if let task = inFlight[chapterID] {
            task.cancel()
            _ = await task.result
            inFlight[chapterID] = nil
        }
        try cache.remove(chapterID)
    }

    @discardableResult
    public func clearReadCache() async throws -> [String] {
        var targets: Set<String> = []
        let cached = cache.cachedChapterIDs()
        for series in items {
            targets.formUnion(series.evictable(cached: cached))
        }
        let ordered = targets.sorted()
        for id in ordered { try await removeCachedChapter(id) }
        return ordered
    }

    public func storageBytes() -> Int64 {
        cache.storageBytes()
    }

    public func sync(now: Date = Date()) async -> SyncReport {
        guard canSave, !isSyncing else { return SyncReport() }
        isSyncing = true
        defer { isSyncing = false }
        var report = SyncReport()

        let ids = items.map(\.id)
        for id in ids {
            if Task.isCancelled { return report }
            guard let snapshot = items.first(where: { $0.id == id }) else { continue }
            do {
                let siteVersion = try await site.refresh(snapshot, now: now)
                if Task.isCancelled { return report }
                guard let index = items.firstIndex(where: { $0.id == id }) else { continue }
                var candidate = items
                candidate[index].applySiteFields(from: siteVersion)
                try commit(candidate)
                report.refreshed.append(id)
            } catch {
                report.failed.append(id)
            }
        }

        let ordered = items.compactMap { series -> (Series, Int)? in
            guard case .new(let unread, _) = series.progress else { return nil }
            return (series, unread)
        }.sorted {
            if $0.1 != $1.1 { return $0.1 > $1.1 }
            if $0.0.views != $1.0.views { return $0.0.views > $1.0.views }
            return $0.0.id < $1.0.id
        }
        for (series, _) in ordered {
            for chapter in series.prefetchCandidates(limit: prefetchLimit) {
                if Task.isCancelled { return report }
                guard !cache.isComplete(chapter.id) else { continue }
                do {
                    _ = try await download(chapter.id)
                    report.downloaded.append(chapter.id)
                } catch {
                    report.failed.append(chapter.id)
                }
            }
        }

        for series in items {
            for id in series.evictable(cached: cache.cachedChapterIDs()).sorted() {
                if Task.isCancelled { return report }
                do {
                    try await removeCachedChapter(id)
                    report.evicted.append(id)
                } catch {
                    report.failed.append(id)
                }
            }
        }
        return report
    }

    /// Downloads each series' newest chapters that are not cached yet, newest first; returns what it downloaded.
    /// Separate from `sync` so the refresh -> unread prefetch -> evict contract stays as it was.
    @discardableResult
    public func downloadLatest() async -> [String] {
        guard canSave else { return [] }
        var downloaded: [String] = []
        for series in items {
            for chapter in series.latestChapters where !cache.isComplete(chapter.id) {
                if Task.isCancelled { return downloaded }
                if (try? await download(chapter.id)) != nil { downloaded.append(chapter.id) }
            }
        }
        return downloaded
    }

    public func pages(
        chapterID: String,
        progress: @escaping @Sendable (Int, Int) -> Void = { _, _ in }
    ) async throws -> [URL] {
        try await download(chapterID, progress: progress)
    }

    private func requireLoad() throws {
        guard canSave else { throw ReaderError.libraryLocked }
    }

    private func commit(_ candidate: [Series]) throws {
        try store.save(candidate)
        items = candidate
        onChange?(candidate)
    }

    private func download(
        _ chapterID: String,
        progress: @escaping @Sendable (Int, Int) -> Void = { _, _ in }
    ) async throws -> [URL] {
        if cache.isComplete(chapterID) { return cache.pages(chapterID) }
        if let task = inFlight[chapterID] { return try await task.value }
        let task = Task { try await downloader.download(chapterID: chapterID, progress: progress) }
        inFlight[chapterID] = task
        do {
            let pages = try await withTaskCancellationHandler {
                try await task.value
            } onCancel: {
                task.cancel()
            }
            inFlight[chapterID] = nil
            return pages
        } catch {
            inFlight[chapterID] = nil
            throw error
        }
    }
}

private extension Series {
    mutating func applySiteFields(from source: Series) {
        name = source.name
        status = source.status
        latestUpdate = source.latestUpdate
        coverURL = source.coverURL
        chapters = source.chapters
        checkedAt = source.checkedAt
    }
}
