import Foundation

public struct ChapterDownloader: Sendable {
    private let http: any HTTPClient
    private let cache: ChapterCache
    private let gap: Duration

    public init(http: any HTTPClient, cache: ChapterCache, gap: Duration = .milliseconds(150)) {
        self.http = http
        self.cache = cache
        self.gap = gap
    }

    public func download(
        chapterID: String,
        progress: @Sendable (Int, Int) -> Void = { _, _ in }
    ) async throws -> [URL] {
        if cache.isComplete(chapterID) { return cache.pages(chapterID) }

        let chapterURL = Site.chapterURL(id: chapterID)
        let (body, status) = try await http.get(chapterURL, headers: Site.pageHeaders)
        guard status == 200 else { throw ReaderError.http(status: status, url: chapterURL) }
        let imageURLs = try SiteParser.imageURLs(String(decoding: body, as: UTF8.self))
        let partial = cache.partial(chapterID)

        do {
            try cache.prepareRoot()
            try? FileManager.default.removeItem(at: partial)
            try FileManager.default.createDirectory(at: partial, withIntermediateDirectories: true)
            for (index, url) in imageURLs.enumerated() {
                try Task.checkCancellation()
                if gap > .zero { try await Task.sleep(for: gap) }
                let (data, imageStatus) = try await http.get(url, headers: Site.imageHeaders)
                try Task.checkCancellation()
                guard imageStatus == 200, !data.isEmpty else {
                    throw ReaderError.http(status: imageStatus, url: url)
                }
                guard data.isRecognizedImage else { throw ReaderError.notAnImage(url: url) }
                let filename = String(format: "%04d.jpg", index + 1)
                try data.write(to: partial.appendingPathComponent(filename))
                progress(index + 1, imageURLs.count)
            }
            try Task.checkCancellation()
            try FileManager.default.moveItem(at: partial, to: cache.directory(chapterID))
            return cache.pages(chapterID)
        } catch {
            try? FileManager.default.removeItem(at: partial)
            throw error
        }
    }
}

private extension Data {
    var isRecognizedImage: Bool {
        starts(with: [0xFF, 0xD8, 0xFF]) ||
            starts(with: [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) ||
            starts(with: Array("GIF87a".utf8)) ||
            starts(with: Array("GIF89a".utf8)) ||
            (count >= 12 && starts(with: Array("RIFF".utf8)) && self[8..<12] == Data("WEBP".utf8))
    }
}
