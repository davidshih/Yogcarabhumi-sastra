import Foundation

public struct LibraryStore: Sendable {
    private let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    public func load() throws -> [Series] {
        let file = directory.appendingPathComponent("library.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        return try JSONDecoder().decode([Series].self, from: Data(contentsOf: file))
    }

    public func save(_ series: [Series]) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(series).write(to: directory.appendingPathComponent("library.json"), options: .atomic)
    }
}
