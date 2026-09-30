import ReaderCore
import SwiftUI
import UniformTypeIdentifiers

private enum LibraryTab: String, CaseIterable, Identifiable {
    case new = "有新話"
    case caught = "已追上"
    case unknown = "待設定"

    var id: Self { self }
}

struct LibraryView: View {
    @Bindable var model: AppModel

    @State private var selectedTab = LibraryTab.new
    @State private var search = ""
    @State private var showAdd = false
    @State private var newSeriesURL = ""
    @State private var showImporter = false
    @State private var alertMessage: String?

    private var counts: [LibraryTab: Int] {
        Dictionary(uniqueKeysWithValues: LibraryTab.allCases.map { tab in
            (tab, model.series.filter { libraryTab(for: $0) == tab }.count)
        })
    }

    private var displayed: [Series] {
        var result = model.series.filter { libraryTab(for: $0) == selectedTab }
        if !search.isEmpty {
            result = result.filter { $0.name.localizedCaseInsensitiveContains(search) }
        }
        result.sort { a, b in
            // Starred series lead every tab.
            if a.isFavorite != b.isFavorite { return a.isFavorite }
            if selectedTab == .new, a.unreadCount != b.unreadCount { return a.unreadCount > b.unreadCount }
            if selectedTab == .new, a.views != b.views { return a.views > b.views }
            return a.name.localizedCompare(b.name) == .orderedAscending
        }
        return result
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                topBar
                masthead
                tabChips
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                DashedRule().padding(.horizontal, 16)

                if displayed.isEmpty {
                    ContentUnavailableView {
                        Label(emptyTitle, systemImage: selectedTab == .new && search.isEmpty ? "checkmark.seal" : "books.vertical")
                            .foregroundStyle(selectedTab == .new && search.isEmpty ? Theme.ok : Theme.ink2)
                    } description: {
                        Text(emptyDescription).foregroundStyle(Theme.ink2)
                    }
                    .frame(maxHeight: .infinity)
                } else {
                    List(displayed) { item in
                        NavigationLink(value: item.id) {
                            LibraryRow(
                                series: item,
                                coverURL: model.coverFile(for: item.id),
                                isCached: item.nextUnread.map { model.cachedChapterIDs.contains($0.id) } ?? false,
                                isDownloading: item.nextUnread.map { model.downloads[$0.id] != nil } ?? false,
                                toggleFavorite: { Task { await model.toggleFavorite(seriesID: item.id) } }
                            )
                        }
                        .listRowBackground(Theme.paper)
                        .listRowSeparator(.hidden)
                        .overlay(alignment: .bottom) { DashedRule().padding(.leading, 100) }
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                    .refreshable { await model.sync() }
                }
            }
            .background(Theme.paper)
            .navigationTitle("追更簿")
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: String.self) { id in
                SeriesView(model: model, seriesID: id)
            }
            .alert("新增漫畫", isPresented: $showAdd) {
                TextField("漫画人作品網址或 slug", text: $newSeriesURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("取消", role: .cancel) {}
                Button("新增") {
                    let input = newSeriesURL
                    newSeriesURL = ""
                    Task { await model.addSeries(input: input) }
                }
            } message: {
                Text("貼上 manhuaren.com 的作品頁網址")
            }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.json]) { result in
                switch result {
                case .success(let url): Task { await model.importSeed(from: url) }
                case .failure(let error): alertMessage = error.localizedDescription
                }
            }
            .alert("追更簿", isPresented: errorPresented) {
                Button("好") {
                    model.lastError = nil
                    alertMessage = nil
                }
            } message: {
                Text(model.lastError ?? alertMessage ?? "未知錯誤")
            }
        }
    }

    /// Search and the menu share the very top row; the system bar is hidden on this screen so nothing sits above them.
    private var topBar: some View {
        HStack(spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.ink3)
                TextField("搜尋漫畫", text: $search)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.search)
                if !search.isEmpty {
                    Button { search = "" } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.ink3)
                    }
                    .accessibilityLabel("清除搜尋")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(Theme.sheet, in: Capsule())
            .overlay(Capsule().strokeBorder(Theme.rule, lineWidth: 1))

            libraryMenu
        }
        .padding(.horizontal, 16)
        .padding(.top, 6)
        .padding(.bottom, 10)
    }

    private var libraryMenu: some View {
        Menu {
            Button("立即同步", systemImage: "arrow.clockwise") {
                Task { await model.sync() }
            }
            .disabled(model.isSyncing)
            Button("新增漫畫", systemImage: "plus") { showAdd = true }
            Button("匯入進度", systemImage: "square.and.arrow.down") { showImporter = true }
            Divider()
            Button("已下載 \(storageText)", systemImage: "internaldrive") {}
                .disabled(true)
            Button("清除已讀快取", systemImage: "trash", role: .destructive) {
                Task { await model.clearReadCache() }
            }
        } label: {
            Group {
                if model.isSyncing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "ellipsis")
                        .font(.body.weight(.bold))
                }
            }
            .foregroundStyle(Theme.ink)
            .frame(width: 40, height: 40)
            .overlay(Circle().strokeBorder(Theme.ink, lineWidth: 1.5))
        }
        .accessibilityLabel("追更簿選單")
    }

    /// Magazine masthead: screentone band, kicker, Mincho title and today's tally.
    private var masthead: some View {
        let unreadSeries = counts[.new, default: 0]
        let unreadChapters = model.series.reduce(0) { $0 + $1.unreadCount }
        return VStack(alignment: .leading, spacing: 6) {
            Text("漫画人 · 追更")
                .font(.caption.weight(.bold))
                .tracking(3)
                .foregroundStyle(Theme.red)
            Text("追更簿")
                .font(Theme.display(40))
                .foregroundStyle(Theme.ink)
            HStack(spacing: 0) {
                Text("共 ").foregroundStyle(Theme.ink2)
                Text("\(model.series.count)").bold()
                Text(" 部 · ").foregroundStyle(Theme.ink2)
                Text("\(unreadSeries)").bold().foregroundStyle(unreadSeries > 0 ? Theme.red : Theme.ink)
                Text(" 部有新話 · ").foregroundStyle(Theme.ink2)
                Text("\(unreadChapters)").bold().foregroundStyle(unreadChapters > 0 ? Theme.red : Theme.ink)
                Text(" 話未讀").foregroundStyle(Theme.ink2)
            }
            .font(.footnote)
            .foregroundStyle(Theme.ink)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.top, 4)
        .padding(.bottom, 12)
        .background(alignment: .topTrailing) {
            Screentone()
                .frame(width: 170, height: 110)
                .mask(LinearGradient(colors: [.black, .clear], startPoint: .topTrailing, endPoint: .bottomLeading))
        }
    }

    /// Ink-outlined pill tabs with counts, the selected one filled with ink.
    private var tabChips: some View {
        HStack(spacing: 8) {
            ForEach(LibraryTab.allCases) { tab in
                let selected = tab == selectedTab
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) { selectedTab = tab }
                } label: {
                    HStack(spacing: 4) {
                        Text(tab.rawValue)
                        Text("\(counts[tab, default: 0])").font(Theme.number(14))
                    }
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .foregroundStyle(selected ? Theme.paper : Theme.ink)
                    .background(selected ? Theme.ink : Color.clear, in: Capsule())
                    .overlay(Capsule().strokeBorder(Theme.ink, lineWidth: 1.5))
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
            Spacer()
        }
    }

    private var errorPresented: Binding<Bool> {
        Binding(
            get: { model.lastError != nil || alertMessage != nil },
            set: { if !$0 { model.lastError = nil; alertMessage = nil } }
        )
    }

    private var storageText: String {
        ByteCountFormatter.string(fromByteCount: model.storageBytes, countStyle: .file)
    }

    private var emptyTitle: String {
        if !search.isEmpty { return "找不到符合的漫畫" }
        switch selectedTab {
        case .new: return "全部追上了"
        case .caught: return "還沒有追上的"
        case .unknown: return "進度都設定好了"
        }
    }

    private var emptyDescription: String {
        if !search.isEmpty { return "換個關鍵字試試。" }
        switch selectedTab {
        case .new: return "有新話的漫畫會出現在這裡。"
        case .caught: return "讀到最新一話的漫畫會出現在這裡。"
        case .unknown: return "找不到上次讀到哪一話的漫畫會出現在這裡。"
        }
    }
}

private struct LibraryRow: View {
    let series: Series
    let coverURL: URL?
    let isCached: Bool
    let isDownloading: Bool
    let toggleFavorite: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            FavoriteStar(isOn: series.isFavorite, action: toggleFavorite)
            CoverImageView(url: coverURL)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Theme.ink.opacity(0.85), lineWidth: 1))
            VStack(alignment: .leading, spacing: 4) {
                Text(series.name)
                    .font(.headline)
                    .foregroundStyle(Theme.ink)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    if isGone {
                        Text(secondaryText)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.ink3)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .overlay(Capsule().strokeBorder(Theme.ink3, lineWidth: 1))
                    } else {
                        Text(secondaryText)
                            .font(.subheadline)
                            .foregroundStyle(Theme.ink2)
                            .lineLimit(1)
                    }
                    if isDownloading {
                        ProgressView().controlSize(.mini)
                    } else if isCached {
                        Image(systemName: "arrow.down.circle.fill")
                            .font(.caption)
                            .foregroundStyle(Theme.ok)
                            .accessibilityLabel("已下載下一話")
                    }
                }
            }
            Spacer(minLength: 8)
            if series.unreadCount > 0 {
                Stamp(count: series.unreadCount)
            }
        }
        .frame(minHeight: 64)
    }

    private var isGone: Bool {
        if case .caught(gone: true) = series.progress { return true }
        return false
    }

    private var secondaryText: String {
        // Gone from the site (已下架 / 页面不存在): nothing to set progress against, so say why.
        if case .caught(gone: true) = series.progress { return series.status.isEmpty ? "已下架" : series.status }
        let read = series.lastRead.map { "讀到 \($0.label)" } ?? "待設定進度"
        return series.latestUpdate.isEmpty ? read : "\(read) · \(series.latestUpdate)"
    }
}

/// Star toggle in front of a title; borderless so tapping it never opens the row.
struct FavoriteStar: View {
    let isOn: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: isOn ? "star.fill" : "star")
                .font(.body.weight(.semibold))
                .foregroundStyle(isOn ? Theme.red : Theme.ink3)
                .frame(width: 28, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .sensoryFeedback(.selection, trigger: isOn)
        .accessibilityLabel(isOn ? "取消最愛" : "加入最愛")
    }
}

private func libraryTab(for series: Series) -> LibraryTab {
    switch series.progress {
    case .new: .new
    case .caught: .caught
    case .unknown: .unknown
    }
}

extension Series {
    var unreadCount: Int {
        if case .new(let unread, _) = progress { return unread }
        return 0
    }

    var nextUnread: Chapter? {
        if case .new(_, let next) = progress { return next }
        return nil
    }
}
