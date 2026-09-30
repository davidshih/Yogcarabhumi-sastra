import ImageIO
import SwiftUI
import UIKit

final class ImageDecoder: @unchecked Sendable {
    static let shared = ImageDecoder()

    private let cache = NSCache<NSString, UIImage>()

    private init() {
        cache.countLimit = 12
    }

    func image(at url: URL, maxPixelSize: Int) async throws -> UIImage {
        let key = "\(url.path)#\(maxPixelSize)" as NSString
        if let cached = cache.object(forKey: key) { return cached }
        let image = try await Task.detached(priority: .userInitiated) {
            guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                  let cgImage = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                    kCGImageSourceCreateThumbnailFromImageAlways: true,
                    kCGImageSourceCreateThumbnailWithTransform: true,
                    kCGImageSourceShouldCacheImmediately: true,
                    kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
                  ] as CFDictionary) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            return UIImage(cgImage: cgImage)
        }.value
        cache.setObject(image, forKey: key)
        return image
    }

    static func pixelSize(at url: URL) -> CGSize? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? CGFloat,
              let height = properties[kCGImagePropertyPixelHeight] as? CGFloat else { return nil }
        return CGSize(width: width, height: height)
    }
}

struct PageImageView: View {
    let url: URL
    let maxPixelSize: Int
    let corruptAction: (() -> Void)?

    @State private var image: UIImage?
    @State private var failed = false

    private var aspectRatio: CGFloat {
        guard let size = ImageDecoder.pixelSize(at: url), size.height > 0 else { return 0.7 }
        return size.width / size.height
    }

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(aspectRatio, contentMode: .fit)
                    .accessibilityLabel("漫畫頁面")
            } else if failed {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle")
                    Text("這頁壞了")
                    if let corruptAction {
                        Button("重新下載", action: corruptAction)
                            .buttonStyle(.borderedProminent)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 240)
                .foregroundStyle(.white)
            } else {
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity)
                    .aspectRatio(aspectRatio, contentMode: .fit)
            }
        }
        .task(id: url) {
            failed = false
            do {
                image = try await ImageDecoder.shared.image(at: url, maxPixelSize: maxPixelSize)
            } catch {
                failed = true
            }
        }
        .onDisappear { image = nil }
    }
}

struct CoverImageView: View {
    let url: URL?
    var width: CGFloat = 44
    var height: CGFloat = 59

    var body: some View {
        Group {
            if let url {
                PageImageView(url: url, maxPixelSize: 132, corruptAction: nil)
            } else {
                Image(systemName: "book.closed")
                    .font(.title2)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: width, height: height)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .accessibilityHidden(true)
    }
}
