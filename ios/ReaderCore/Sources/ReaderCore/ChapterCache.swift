import Foundation

public struct ChapterCache: Sendable {
    let root: URL

    public init(root: URL) {
        self.root = root
    }

    public func isComplete(_ chapterID: String) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: directory(chapterID).path, isDirectory: &isDirectory) && isDirectory.boolValue
    }

    public func pages(_ chapterID: String) -> [URL] {
        guard isComplete(chapterID) else { return [] }
        return ((try? FileManager.default.contentsOfDirectory(
            at: directory(chapterID),
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []).sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    public func remove(_ chapterID: String) throws {
        let path = directory(chapterID)
        if FileManager.default.fileExists(atPath: path.path) {
            try FileManager.default.removeItem(at: path)
        }
        let partialPath = partial(chapterID)
        if FileManager.default.fileExists(atPath: partialPath.path) {
            try FileManager.default.removeItem(at: partialPath)
        }
    }

    public func cachedChapterIDs() -> Set<String> {
        guard let contents = try? FileManager.default.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return Set(contents.compactMap { url in
            guard !url.lastPathComponent.hasSuffix(".partial"),
                  (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true else { return nil }
            return url.lastPathComponent
        })
    }

    func prepareRoot() throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var mutableRoot = root
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? mutableRoot.setResourceValues(values)
    }

    func directory(_ chapterID: String) -> URL {
        root.appendingPathComponent(chapterID, isDirectory: true)
    }

    func partial(_ chapterID: String) -> URL {
        root.appendingPathComponent("\(chapterID).partial", isDirectory: true)
    }

    func storageBytes() -> Int64 {
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey]
        ) else { return 0 }
        var total: Int64 = 0
        for case let file as URL in enumerator {
            guard let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
                  values.isRegularFile == true else { continue }
            total += Int64(values.fileSize ?? 0)
        }
        return total
    }
}
