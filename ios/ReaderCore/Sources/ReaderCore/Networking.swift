import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public protocol HTTPClient: Sendable {
    func get(_ url: URL, headers: [String: String]) async throws -> (Data, Int)
}

public struct URLSessionClient: HTTPClient {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func get(_ url: URL, headers: [String: String]) async throws -> (Data, Int) {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        let (data, response) = try await session.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }
}

public struct SiteClient: Sendable {
    private let http: any HTTPClient
    private let gap: Duration

    public init(http: any HTTPClient, gap: Duration = .milliseconds(500)) {
        self.http = http
        self.gap = gap
    }

    public func refresh(_ series: Series, now: Date = Date()) async throws -> Series {
        guard series.id.range(of: #"^m\d+$"#, options: .regularExpression) == nil else { return series }
        if gap > .zero { try await Task.sleep(for: gap) }
        let url = Site.seriesURL(slug: series.id)
        let (data, status) = try await http.get(url, headers: Site.pageHeaders)
        var refreshed = series
        refreshed.checkedAt = now
        if status == 404 {
            refreshed.chapters = []
            refreshed.status = "页面不存在"
            return refreshed
        }
        guard status == 200 else { throw ReaderError.http(status: status, url: url) }
        let page = SiteParser.parseSeries(String(decoding: data, as: UTF8.self), now: now)
        guard !page.chapters.isEmpty else {
            refreshed.chapters = []
            refreshed.status = "已下架"
            return refreshed
        }
        if !page.name.isEmpty { refreshed.name = page.name }
        refreshed.status = page.status
        refreshed.latestUpdate = page.latestUpdate
        refreshed.coverURL = page.coverURL
        refreshed.chapters = page.chapters
        return refreshed
    }
}
