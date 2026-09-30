import Foundation

public enum SeedImporter {
    public static func decode(_ data: Data) throws -> [Series] {
        let raw = try JSONSerialization.jsonObject(with: data)
        guard let elements = raw as? [Any] else {
            throw DecodingError.typeMismatch(
                [Any].self,
                .init(codingPath: [], debugDescription: "Seed must be a JSON array")
            )
        }

        var seen: Set<String> = []
        var result: [Series] = []
        for element in elements {
            guard let wrapper = element as? [String: Any] else { continue }
            let document = (wrapper["data"] as? [String: Any]) ?? wrapper
            guard let id = document["slug"] as? String, !id.isEmpty, seen.insert(id).inserted else { continue }
            let chapters = decodeChapters(document["chapters"])
            let lastRead = decodeChapter(document["last_read"])
            let name = (document["name"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? id
            result.append(Series(
                id: id,
                name: name,
                chapters: chapters,
                lastRead: lastRead,
                status: document["status"] as? String ?? "",
                latestUpdate: document["latest_update"] as? String ?? "",
                readAt: decodeDate(document["read_at"]),
                views: document["views"] as? Int ?? 0,
                checkedAt: decodeDate(document["checked_at"])
            ))
        }
        return result
    }

    public static func merge(existing: [Series], imported: [Series]) -> [Series] {
        var ids = Set(existing.map(\.id))
        return existing + imported.filter { ids.insert($0.id).inserted }
    }
}

private func decodeChapter(_ value: Any?) -> Chapter? {
    guard let raw = value as? [String: Any],
          let id = raw["id"] as? String,
          let label = raw["label"] as? String else { return nil }
    return Chapter(id: id, label: label)
}

private func decodeChapters(_ value: Any?) -> [Chapter] {
    guard let raw = value as? [Any] else { return [] }
    return raw.compactMap(decodeChapter)
}

private func decodeDate(_ value: Any?) -> Date? {
    guard let text = value as? String else { return nil }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: text) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: text)
}
