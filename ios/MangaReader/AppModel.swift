import Foundation
import Observation
import ReaderCore

struct DownloadProgress: Equatable {
    var done: Int
    var total: Int
}

enum AppPhase: Equatable {
    case starting
    case ready
    case failed(String)
}

@MainActor
@Observable
final class AppModel {
    static let autoSyncInterval: TimeInterval = 6 * 60 * 60

    private let library: Library
    private let coversDirectory: URL
    private let defaults: UserDefaults
    private var startTask: Task<Void, Never>?
    private var resumeTask: Task<Void, Never>?
    private var pendingResume: (seriesID: String, chapterID: String, page: Int?)?

    var series: [Series] = []
    var downloads: [String: DownloadProgress] = [:]
    /// Bulk downloads started from a series page: series id -> chapters done / total.
    var bulkProgress: [String: DownloadProgress] = [:]
    private var bulkTasks: [String: Task<Void, Never>] = [:]
    var cachedChapterIDs: Set<String> = []
    var storageBytes: Int64 = 0
    var isSyncing = false
    var lastSyncAt: Date?
    var phase: AppPhase = .starting
    var lastError: String?

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let root = applicationSupport.appendingPathComponent("Reader", isDirectory: true)
        let cache = ChapterCache(root: root.appendingPathComponent("chapters", isDirectory: true))
        let http = URLSessionClient()
        library = Library(
            store: LibraryStore(directory: root),
            cache: cache,
            site: SiteClient(http: http),
            downloader: ChapterDownloader(http: http, cache: cache),
            prefetchLimit: 3
        )
        coversDirectory = root.appendingPathComponent("covers", isDirectory: true)
        lastSyncAt = defaults.object(forKey: "lastSyncAt") as? Date
    }

    var needsAutoSync: Bool {
        guard let lastSyncAt else { return true }
        return Date().timeIntervalSince(lastSyncAt) >= Self.autoSyncInterval
    }

    func start() async {
        if let startTask {
            await startTask.value
            return
        }
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.performStart()
        }
        startTask = task
        await task.value
    }

    private func performStart() async {
        do {
            try await library.load()
            await library.setOnChange { [weak self] updated in
                Task { @MainActor in
                    self?.series = updated
                }
            }
#if DEBUG
            if let index = CommandLine.arguments.firstIndex(of: "-importSeed"),
               CommandLine.arguments.indices.contains(index + 1) {
                let url = URL(fileURLWithPath: CommandLine.arguments[index + 1])
                let data = try Data(contentsOf: url)
                _ = try await library.importSeed(data)
            }
#endif
            phase = .ready
            await refreshSnapshot()
            if needsAutoSync { await sync() }
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    func importSeed(from url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            _ = try await library.importSeed(Data(contentsOf: url))
            await refreshSnapshot()
        } catch {
            lastError = "匯入失敗：\(error.localizedDescription)"
        }
    }

    func addSeries(input: String) async {
        guard let slug = Site.slug(from: input) else {
            lastError = "不是漫画人的作品網址"
            return
        }
        do {
            if try await library.add(Series(id: slug, name: slug)) {
                try await library.refresh(slug)
            }
            await refreshSnapshot()
        } catch {
            lastError = "新增失敗：\(error.localizedDescription)"
        }
    }

    func removeSeries(_ id: String) async {
        do {
            try await library.remove(id)
            await refreshSnapshot()
        } catch {
            lastError = "刪除失敗：\(error.localizedDescription)"
        }
    }

    func setLastRead(seriesID: String, chapter: Chapter?) async {
        do {
            try await library.update(seriesID) { $0.setLastRead(chapter, at: Date()) }
            await refreshSnapshot()
        } catch {
            lastError = "無法儲存進度：\(error.localizedDescription)"
        }
    }

    func markFinished(seriesID: String, chapter: Chapter) async {
        resumeTask?.cancel()
        pendingResume = nil
        do {
            try await library.update(seriesID) {
                $0.markFinished(chapter, at: Date())
                $0.resumePage[chapter.id] = nil
            }
            await refreshSnapshot()
        } catch {
            lastError = "無法儲存進度：\(error.localizedDescription)"
        }
    }

    func setMode(seriesID: String, mode: ReadingMode) async {
        do {
            try await library.update(seriesID) { $0.readingMode = mode }
            await refreshSnapshot()
        } catch {
            lastError = "無法儲存閱讀模式：\(error.localizedDescription)"
        }
    }

    func setResumePage(seriesID: String, chapterID: String, page: Int?) {
        if let index = series.firstIndex(where: { $0.id == seriesID }) {
            series[index].resumePage[chapterID] = page
        }
        pendingResume = (seriesID, chapterID, page)
        resumeTask?.cancel()
        resumeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(1))
            guard !Task.isCancelled else { return }
            await self?.flushResumeSaves()
        }
    }

    func flushResumeSaves() async {
        resumeTask?.cancel()
        guard let pending = pendingResume else { return }
        pendingResume = nil
        do {
            try await library.update(pending.seriesID) { series in
                series.resumePage[pending.chapterID] = pending.page
            }
            await refreshSnapshot()
        } catch {
            lastError = "無法儲存閱讀位置：\(error.localizedDescription)"
        }
    }

    func sync() async {
        guard phase == .ready, !isSyncing else { return }
        isSyncing = true
        let report = await library.sync()
        // Every series keeps its two newest chapters on the phone, read or not.
        await library.downloadLatest()
        let now = Date()
        lastSyncAt = now
        defaults.set(now, forKey: "lastSyncAt")
        isSyncing = false
        await refreshSnapshot()
        await downloadMissingCovers()
        if !report.failed.isEmpty {
            lastError = "同步完成，\(report.failed.count) 個項目失敗"
        }
    }

    func pages(for chapter: Chapter) async throws -> [URL] {
        defer {
            downloads[chapter.id] = nil
            Task { await refreshCacheSnapshot() }
        }
        return try await library.pages(chapterID: chapter.id) { [weak self] done, total in
            Task { @MainActor in
                self?.downloads[chapter.id] = DownloadProgress(done: done, total: total)
            }
        }
    }

    func downloadChapter(_ chapter: Chapter) async {
        do {
            _ = try await pages(for: chapter)
        } catch {
            lastError = "下載失敗：\(error.localizedDescription)"
        }
    }

    func toggleFavorite(seriesID: String) async {
        do {
            try await library.update(seriesID) { $0.isFavorite.toggle() }
            await refreshSnapshot()
        } catch {
            lastError = "無法儲存最愛：\(error.localizedDescription)"
        }
    }

    /// Pins the chapters so sync never evicts them, then downloads them one at a time in reading order.
    func downloadChapters(seriesID: String, chapters: [Chapter]) {
        guard bulkTasks[seriesID] == nil, !chapters.isEmpty else { return }
        let ids = Set(chapters.map(\.id))
        bulkProgress[seriesID] = DownloadProgress(done: 0, total: chapters.count)
        bulkTasks[seriesID] = Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                self.bulkTasks[seriesID] = nil
                self.bulkProgress[seriesID] = nil
            }
            do {
                try await self.library.update(seriesID) { $0.pinnedChapters.formUnion(ids) }
                await self.refreshSnapshot()
            } catch {
                self.lastError = "無法開始下載：\(error.localizedDescription)"
                return
            }
            var failed = 0
            for (index, chapter) in chapters.enumerated() {
                if Task.isCancelled { break }
                self.bulkProgress[seriesID] = DownloadProgress(done: index, total: chapters.count)
                do {
                    _ = try await self.pages(for: chapter)
                } catch {
                    if Task.isCancelled { break }
                    failed += 1
                }
            }
            if failed > 0 { self.lastError = "有 \(failed) 話下載失敗，稍後可以再試一次。" }
        }
    }

    func cancelBulkDownload(seriesID: String) {
        bulkTasks[seriesID]?.cancel()
    }

    func removeCachedChapter(_ chapterID: String) async {
        do {
            // A chapter deleted by hand is no longer kept on purpose.
            if let owner = series.first(where: { $0.pinnedChapters.contains(chapterID) }) {
                try await library.update(owner.id) { $0.pinnedChapters.remove(chapterID) }
                await refreshSnapshot()
            }
            try await library.removeCachedChapter(chapterID)
            await refreshCacheSnapshot()
        } catch {
            lastError = "刪除快取失敗：\(error.localizedDescription)"
        }
    }

    func clearReadCache() async {
        do {
            _ = try await library.clearReadCache()
            await refreshCacheSnapshot()
        } catch {
            lastError = "清除快取失敗：\(error.localizedDescription)"
        }
    }

    func coverFile(for seriesID: String) -> URL? {
        let url = coversDirectory.appendingPathComponent("\(seriesID).jpg")
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private func refreshSnapshot() async {
        series = await library.series
        await refreshCacheSnapshot()
    }

    private func refreshCacheSnapshot() async {
        cachedChapterIDs = await library.cachedChapterIDs()
        storageBytes = await library.storageBytes()
    }

    private func downloadMissingCovers() async {
        try? FileManager.default.createDirectory(at: coversDirectory, withIntermediateDirectories: true)
        var directory = coversDirectory
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? directory.setResourceValues(values)
        for item in series {
            guard coverFile(for: item.id) == nil, let source = item.coverURL else { continue }
            var request = URLRequest(url: source)
            for (name, value) in Site.imageHeaders { request.setValue(value, forHTTPHeaderField: name) }
            guard let (data, response) = try? await URLSession.shared.data(for: request),
                  (response as? HTTPURLResponse)?.statusCode == 200,
                  !data.isEmpty else { continue }
            try? data.write(to: coversDirectory.appendingPathComponent("\(item.id).jpg"), options: .atomic)
        }
    }
}
