import SwiftUI
import UIKit

/// The tracker page's weekly-magazine look: paper, ink, a vermilion "+N" stamp and screentone.
/// Same palette as tracker_page.html so the app and the old page read as one product.
enum Theme {
    static let paper = dynamic(light: 0xF6F6F3, dark: 0x131316)
    static let sheet = dynamic(light: 0xFFFFFF, dark: 0x1C1C21)
    static let ink = dynamic(light: 0x17171B, dark: 0xECECEF)
    static let ink2 = dynamic(light: 0x55555F, dark: 0xB1B1BB)
    static let ink3 = dynamic(light: 0x8A8A94, dark: 0x7C7C87)
    static let rule = dynamic(light: 0xDCDCD6, dark: 0x2E2E35)
    static let tone = dynamic(light: 0xC9C9C2, dark: 0x34343C)
    static let red = dynamic(light: 0xC8102E, dark: 0xFF5A6E)
    static let ok = dynamic(light: 0x2F7A4F, dark: 0x7FCF9C)

    /// Mincho for the masthead only; series names stay in the system font for full CJK coverage.
    static func display(_ size: CGFloat) -> Font { .custom("HiraMinProN-W6", size: size) }
    static func number(_ size: CGFloat) -> Font { .system(size: size, weight: .heavy, design: .rounded).monospacedDigit() }

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

/// The red "+N" stamp that leads every series with new chapters, tilted like a rubber stamp.
struct Stamp: View {
    let count: Int
    var size: CGFloat = 50

    var body: some View {
        VStack(spacing: -2) {
            Text("+\(count)")
                .font(Theme.number(size * 0.36))
                .minimumScaleFactor(0.6)
                .lineLimit(1)
            Text("話")
                .font(.system(size: size * 0.2, weight: .bold))
        }
        .foregroundStyle(Theme.red)
        .frame(width: size, height: size)
        .overlay(Circle().strokeBorder(Theme.red, lineWidth: 2.5))
        .overlay(Circle().inset(by: 4.5).stroke(Theme.red.opacity(0.55), lineWidth: 0.8))
        .rotationEffect(.degrees(-8))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(count) 話未讀")
    }
}

/// Halftone dots, the texture of the tracker page's masthead.
struct Screentone: View {
    var spacing: CGFloat = 6
    var radius: CGFloat = 1.1

    var body: some View {
        Canvas { context, size in
            var y: CGFloat = 0
            var row = 0
            while y < size.height + spacing {
                var x: CGFloat = row.isMultiple(of: 2) ? 0 : spacing / 2
                while x < size.width + spacing {
                    context.fill(Path(ellipseIn: CGRect(x: x - radius, y: y - radius, width: radius * 2, height: radius * 2)),
                                 with: .color(Theme.tone))
                    x += spacing
                }
                y += spacing * 0.866
                row += 1
            }
        }
        .accessibilityHidden(true)
    }
}

/// A dashed hairline between rows, like a printed table of contents.
struct DashedRule: View {
    var body: some View {
        Line()
            .stroke(Theme.rule, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    private struct Line: Shape {
        func path(in rect: CGRect) -> Path {
            Path { $0.move(to: CGPoint(x: rect.minX, y: rect.midY)); $0.addLine(to: CGPoint(x: rect.maxX, y: rect.midY)) }
        }
    }
}
