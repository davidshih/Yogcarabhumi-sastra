import Foundation
import XCTest
@testable import ReaderCore

final class ReaderCoreTests: XCTestCase {
    func testProgressPrefetchAndEviction() {
        let chapters = (1...5).reversed().map { Chapter(id: "m\($0)", label: "第\($0)话") }
        var series = Series(id: "x", name: "X", chapters: chapters, lastRead: chapters.last)
        XCTAssertEqual(series.progress, .new(unread: 4, next: chapters[3]))
        XCTAssertEqual(series.prefetchCandidates(limit: 2), [chapters[3], chapters[2]])
        series.lastRead = chapters[2]
        XCTAssertEqual(series.evictable(cached: ["m1", "m2", "m4"]), ["m1", "m2"])
    }

    // A library.json written before favorites and pins existed must still load, with both off.
    func testLegacyLibraryDecodesWithoutNewFields() throws {
        let legacy = #"{"id":"x","name":"X","chapters":[{"id":"m1","label":"1"}],"status":"","latestUpdate":"","views":3,"readingMode":"vertical","resumePage":{}}"#
        let series = try JSONDecoder().decode(Series.self, from: Data(legacy.utf8))
        XCTAssertEqual(series.id, "x")
        XCTAssertEqual(series.views, 3)
        XCTAssertFalse(series.isFavorite)
        XCTAssertEqual(series.pinnedChapters, [])
    }

    func testPinnedChaptersAreNeverEvicted() throws {
        // Six chapters so m1-m3 sit outside the newest-two rule and only the pin decides.
        let chapters = (1...6).reversed().map { Chapter(id: "m\($0)", label: "\($0)") }
        let series = Series(id: "x", name: "X", chapters: chapters, lastRead: chapters[0],
                            isFavorite: true, pinnedChapters: ["m2"])
        XCTAssertEqual(series.evictable(cached: ["m1", "m2", "m3"]), ["m1", "m3"])
        let roundTrip = try JSONDecoder().decode(Series.self, from: JSONEncoder().encode(series))
        XCTAssertEqual(roundTrip, series)
    }

    func testNewestChaptersAreKeptAndDownloaded() async throws {
        let chapters = (1...5).reversed().map { Chapter(id: "m\($0)", label: "\($0)") }
        let caughtUp = Series(id: "x", name: "X", chapters: chapters, lastRead: chapters[0])
        XCTAssertEqual(caughtUp.latestChapters.map(\.id), ["m5", "m4"])
        // Even when read, the two newest stay; older read chapters go.
        XCTAssertEqual(caughtUp.evictable(cached: ["m5", "m4", "m3", "m1"]), ["m3", "m1"])

        let chapterHTML = try fixture("chapter.html")
        let http = FakeHTTP { url in
            if url.host == "www.manhuaren.com" { return (Data(chapterHTML.utf8), 200) }
            return (Data([0xFF, 0xD8, 0xFF, 0x00]), 200)
        }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        try LibraryStore(directory: dir).save([caughtUp, Series(id: "gone", name: "G")])
        let cache = ChapterCache(root: dir.appendingPathComponent("chapters"))
        let library = Library(store: LibraryStore(directory: dir), cache: cache,
                              site: SiteClient(http: http, gap: .zero),
                              downloader: ChapterDownloader(http: http, cache: cache, gap: .zero))
        try await library.load()
        let first = await library.downloadLatest()
        XCTAssertEqual(first, ["m5", "m4"])
        XCTAssertEqual(cache.cachedChapterIDs(), ["m5", "m4"])
        let second = await library.downloadLatest()
        XCTAssertEqual(second, [], "already cached chapters are not downloaded again")
    }

    func testMarkFinishedOnlyAdvances() {
        let newest = Chapter(id: "m3", label: "3")
        let middle = Chapter(id: "m2", label: "2")
        let oldest = Chapter(id: "m1", label: "1")
        var series = Series(id: "x", name: "X", chapters: [newest, middle, oldest], lastRead: middle)
        series.markFinished(oldest, at: Date(timeIntervalSince1970: 1))
        XCTAssertEqual(series.lastRead, middle)
        series.markFinished(newest, at: Date(timeIntervalSince1970: 2))
        XCTAssertEqual(series.lastRead, newest)
    }

    func testSeriesFixtureAndPacker() throws {
        let seriesHTML = try fixture("series.html")
        let page = SiteParser.parseSeries(seriesHTML, now: Date())
        XCTAssertEqual(page.chapters.count, 24)
        XCTAssertEqual(page.chapters.first?.id, "m1822868")
        XCTAssertEqual(page.coverURL?.host, "mhfm8us.cdndm5.com")
        let imageURLs = try SiteParser.imageURLs(try fixture("chapter.html"))
        XCTAssertEqual(imageURLs.count, 176)
        XCTAssertEqual(imageURLs.first?.lastPathComponent, "1_2452.jpg")
    }

    func testSlugValidation() {
        XCTAssertEqual(Site.slug(from: "www.manhuaren.com/manhua-grand-blue"), "grand-blue")
        XCTAssertEqual(Site.slug(from: "grand-blue"), "grand-blue")
        XCTAssertNil(Site.slug(from: "https://evilmanhuaren.com/manhua-x/"))
        XCTAssertNil(Site.slug(from: "https://www.manhuaren.com/m1/"))
    }

    func testSeedDecodeAndMerge() throws {
        let data = Data(#"[{"slug":"a","chapters":[]},{"data":{"slug":"b","name":"B","chapters":[]}}, {"slug":"a","name":"duplicate"}]"#.utf8)
        let decoded = try SeedImporter.decode(data)
        XCTAssertEqual(decoded.map(\.id), ["a", "b"])
        XCTAssertEqual(decoded.first?.name, "a")
        XCTAssertEqual(SeedImporter.merge(existing: [Series(id: "b", name: "Mine")], imported: decoded).map(\.id), ["b", "a"])
    }

    func testStoreRoundTrip() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LibraryStore(directory: directory)
        let original = [Series(id: "x", name: "X", readingMode: .pagedRTL, resumePage: ["m1": 3])]
        try store.save(original)
        XCTAssertEqual(try store.load(), original)
    }

    // Removed series come back with full chapter lists for en-US requests; every request must pin zh-CN.
    func testEveryRequestPinsChineseLocale() async throws {
        let chapterHTML = try fixture("chapter.html")
        let seriesHTML = try fixture("series.html")
        let http = FakeHTTP { url in
            if url.path.hasPrefix("/manhua-") { return (Data(seriesHTML.utf8), 200) }
            if url.host == "www.manhuaren.com" { return (Data(chapterHTML.utf8), 200) }
            return (Data([0xFF, 0xD8, 0xFF, 0x00]), 200)
        }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        _ = try await SiteClient(http: http, gap: .zero).refresh(Series(id: "x", name: "X"))
        _ = try await ChapterDownloader(http: http, cache: ChapterCache(root: root), gap: .zero).download(chapterID: "m1822868")
        let sent = await http.sentHeaders
        XCTAssertEqual(sent.count, 178)
        XCTAssertTrue(sent.allSatisfy { $0["Accept-Language"] == "zh-CN,zh;q=0.9" })
        XCTAssertTrue(sent.dropFirst(2).allSatisfy { $0["Referer"] == Site.referer })
    }

    func testDownloaderCachesAndReusesPages() async throws {
        let chapterHTML = try fixture("chapter.html")
        let http = FakeHTTP { url in
            if url.host == "www.manhuaren.com" { return (Data(chapterHTML.utf8), 200) }
            return (Data([0xFF, 0xD8, 0xFF, 0x00]), 200)
        }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = ChapterCache(root: root)
        let downloader = ChapterDownloader(http: http, cache: cache, gap: .zero)
        let firstCount = try await downloader.download(chapterID: "m1822868").count
        let secondCount = try await downloader.download(chapterID: "m1822868").count
        let requestCount = await http.requestCount
        XCTAssertEqual(firstCount, 176)
        XCTAssertEqual(secondCount, 176)
        XCTAssertEqual(requestCount, 177)
    }

    func testLibraryRequiresLoadAndPersistsChanges() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = ChapterCache(root: directory.appendingPathComponent("chapters"))
        let http = FakeHTTP { _ in (Data(), 500) }
        let library = Library(
            store: LibraryStore(directory: directory),
            cache: cache,
            site: SiteClient(http: http, gap: .zero),
            downloader: ChapterDownloader(http: http, cache: cache, gap: .zero)
        )
        do {
            _ = try await library.add(Series(id: "x", name: "X"))
            XCTFail("Expected a locked library")
        } catch ReaderError.libraryLocked {}
        try await library.load()
        let added = try await library.add(Series(id: "x", name: "X"))
        let ids = await library.series.map(\.id)
        XCTAssertTrue(added)
        XCTAssertEqual(ids, ["x"])
    }

    private func fixture(_ name: String) throws -> String {
        let url = Bundle.module.url(forResource: name, withExtension: nil, subdirectory: "Fixtures")!
        return try String(contentsOf: url, encoding: .utf8)
    }
}

private actor FakeHTTP: HTTPClient {
    private let response: @Sendable (URL) async throws -> (Data, Int)
    private var requests = 0
    private var headerLog: [[String: String]] = []

    init(response: @escaping @Sendable (URL) async throws -> (Data, Int)) {
        self.response = response
    }

    func get(_ url: URL, headers: [String: String]) async throws -> (Data, Int) {
        requests += 1
        headerLog.append(headers)
        return try await response(url)
    }

    var requestCount: Int { requests }
    var sentHeaders: [[String: String]] { headerLog }
}
