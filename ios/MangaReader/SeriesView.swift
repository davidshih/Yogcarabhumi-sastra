import ReaderCore
import SwiftUI

private struct ReaderSelection: Identifiable {
    let id = UUID()
    let chapter: Chapter
}

struct SeriesView: View {
    @Bindable var model: AppModel
    let seriesID: String

    @Environment(\.dismiss) private var dismiss
    @State private var readerSelection: ReaderSelection?
    @State private var confirmRemoval = false
    @State private var confirmDownloadAll = false
    @State private var selecting = false
    @State private var selected: Set<String> = []

    private var series: Series? {
        model.series.first(where: { $0.id == seriesID })
    }

    var body: some View {
        Group {
            if let series {
                List {
                    Section {
                        header(series)
                    }
                    .listRowBackground(Theme.sheet)
                    Section {
                        if let next = series.nextUnread {
                            Button {
                                readerSelection = ReaderSelection(chapter: next)
                            } label: {
                                Label("繼續讀 \(next.label)", systemImage: "book.fill")
                                    .frame(maxWidth: .infinity, minHeight: 44)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(Theme.red)
                            .listRowBackground(Color.clear)
                        }

                        if let bulk = model.bulkProgress[series.id] {
                            bulkProgressRow(series, bulk)
                                .listRowBackground(Theme.sheet)
                        }

                        Picker("閱讀模式", selection: modeBinding(series)) {
                            Text("直向").tag(ReadingMode.vertical)
                            Text("翻頁").tag(ReadingMode.pagedRTL)
                        }
                        .pickerStyle(.segmented)
                        .listRowBackground(Theme.sheet)
                    }
                    Section {
                        ForEach(Array(series.chapters.enumerated()), id: \.element.id) { index, chapter in
                            Button {
                                if selecting {
                                    if selected.contains(chapter.id) { selected.remove(chapter.id) } else { selected.insert(chapter.id) }
                                } else {
                                    readerSelection = ReaderSelection(chapter: chapter)
                                }
                            } label: {
                                chapterRow(series: series, chapter: chapter, index: index)
                            }
                            .buttonStyle(.plain)
                            .listRowBackground(Theme.sheet)
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                Button("讀到這裡") {
                                    Task { await model.setLastRead(seriesID: series.id, chapter: chapter) }
                                }
                                .tint(Theme.ink)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                if model.cachedChapterIDs.contains(chapter.id) {
                                    Button("刪除", role: .destructive) {
                                        Task { await model.removeCachedChapter(chapter.id) }
                                    }
                                } else {
                                    Button("下載") {
                                        Task { await model.downloadChapter(chapter) }
                                    }
                                    .tint(Theme.ok)
                                }
                            }
                        }
                    } header: {
                        Text("章節 · 共 \(series.chapters.count) 話")
                            .font(.caption.weight(.bold))
                            .tracking(2)
                            .foregroundStyle(Theme.ink2)
                    }
                }
                .scrollContentBackground(.hidden)
                .background(Theme.paper)
                .toolbarBackground(Theme.paper, for: .navigationBar)
                .navigationTitle(series.name)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("移除", systemImage: "trash", role: .destructive) {
                            confirmRemoval = true
                        }
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    if selecting { selectionBar(series) }
                }
                .confirmationDialog("下載全部 \(missingChapters(series).count) 話？", isPresented: $confirmDownloadAll, titleVisibility: .visible) {
                    Button("下載 \(missingChapters(series).count) 話") {
                        model.downloadChapters(seriesID: series.id, chapters: missingChapters(series))
                    }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("依閱讀順序一話一話下載。手動下載的章節會保留，不會被自動清除。")
                }
                .confirmationDialog("要從追更簿移除這部作品嗎？", isPresented: $confirmRemoval, titleVisibility: .visible) {
                    Button("移除 \(series.name)", role: .destructive) {
                        Task {
                            await model.removeSeries(series.id)
                            dismiss()
                        }
                    }
                    Button("取消", role: .cancel) {}
                }
                .fullScreenCover(item: $readerSelection) { selection in
                    ReaderView(model: model, seriesID: series.id, initialChapter: selection.chapter)
                }
            } else {
                ContentUnavailableView("找不到作品", systemImage: "books.vertical")
            }
        }
    }

    private func header(_ series: Series) -> some View {
        HStack(alignment: .top, spacing: 12) {
            FavoriteStar(isOn: series.isFavorite) {
                Task { await model.toggleFavorite(seriesID: series.id) }
            }
            CoverImageView(url: model.coverFile(for: series.id), width: 72, height: 96)
            VStack(alignment: .leading, spacing: 8) {
                Text(series.name)
                    .font(.title3.bold())
                    .foregroundStyle(Theme.ink)
                Text([series.status, series.latestUpdate].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink2)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                progressLabel(series)
                    .font(.subheadline.weight(.medium))
            }
            Spacer(minLength: 0)
            VStack(spacing: 12) {
                downloadMenu(series)
                if series.unreadCount > 0 {
                    Stamp(count: series.unreadCount, size: 50)
                }
            }
        }
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private func progressLabel(_ series: Series) -> some View {
        switch series.progress {
        case .new(let unread, _):
            Label("還有 \(unread) 話", systemImage: "clock").foregroundStyle(Theme.red)
        case .caught(let gone):
            Label(gone ? "作品已下架" : "已追上最新", systemImage: gone ? "nosign" : "checkmark.seal")
                .foregroundStyle(gone ? Theme.ink3 : Theme.ok)
        case .unknown(let missing):
            Label(missing ? "原進度不在章節中" : "待設定進度", systemImage: "questionmark.circle")
                .foregroundStyle(Theme.ink2)
        }
    }

    /// Chapters not on the phone yet, oldest first so a bulk download follows reading order.
    private func missingChapters(_ series: Series) -> [Chapter] {
        series.chapters.reversed().filter { !model.cachedChapterIDs.contains($0.id) }
    }

    /// Bulk-download actions live behind a "⋯" next to the cover, out of the way of reading.
    private func downloadMenu(_ series: Series) -> some View {
        let missing = missingChapters(series).count
        return Menu {
            Button(missing == 0 ? "已全部下載" : "全部下載（\(missing) 話）", systemImage: "arrow.down.circle") {
                confirmDownloadAll = true
            }
            .disabled(missing == 0 || model.bulkProgress[series.id] != nil)
            Button("選擇章節下載", systemImage: "checklist") {
                selected = []
                withAnimation { selecting = true }
            }
            .disabled(series.chapters.isEmpty || model.bulkProgress[series.id] != nil)
        } label: {
            Image(systemName: "ellipsis")
                .font(.body.weight(.bold))
                .foregroundStyle(Theme.ink)
                .frame(width: 36, height: 36)
                .overlay(Circle().strokeBorder(Theme.ink, lineWidth: 1.5))
        }
        .buttonStyle(.borderless)
        .accessibilityLabel("下載選項")
    }

    private func bulkProgressRow(_ series: Series, _ bulk: DownloadProgress) -> some View {
        HStack(spacing: 12) {
            ProgressView(value: Double(bulk.done), total: Double(max(bulk.total, 1)))
                .tint(Theme.red)
            Text("下載中 \(bulk.done)/\(bulk.total) 話")
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(Theme.ink2)
            Button("停止") { model.cancelBulkDownload(seriesID: series.id) }
                .buttonStyle(.bordered)
        }
    }

    private func selectionBar(_ series: Series) -> some View {
        let picked = series.chapters.reversed().filter { selected.contains($0.id) }
        return HStack(spacing: 12) {
            Button("取消") { withAnimation { selecting = false } }
            Spacer()
            Button("全選未下載") { selected = Set(missingChapters(series).map(\.id)) }
            Button {
                model.downloadChapters(seriesID: series.id, chapters: picked)
                withAnimation { selecting = false }
            } label: {
                Text("下載 \(picked.count) 話").bold()
            }
            .buttonStyle(.borderedProminent)
            .disabled(picked.isEmpty)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private func chapterRow(series: Series, chapter: Chapter, index: Int) -> some View {
        HStack {
            if selecting {
                Image(systemName: selected.contains(chapter.id) ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(selected.contains(chapter.id) ? Theme.red : Theme.ink3)
                    .font(.title3)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(chapter.label)
                    .foregroundStyle(isRead(series: series, index: index) ? Theme.ink3 : Theme.ink)
                if let progress = model.downloads[chapter.id] {
                    Text("下載中 \(progress.done)/\(progress.total)")
                        .font(.caption)
                        .foregroundStyle(Theme.ink2)
                }
            }
            Spacer()
            if series.lastRead?.id == chapter.id {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Theme.ok)
                    .accessibilityLabel("目前進度")
            } else if model.downloads[chapter.id] != nil {
                ProgressView().controlSize(.small)
            } else if model.cachedChapterIDs.contains(chapter.id) {
                Image(systemName: "arrow.down.circle.fill")
                    .foregroundStyle(Theme.ink3)
                    .accessibilityLabel("已下載")
            }
        }
        .contentShape(Rectangle())
        .frame(minHeight: 44)
    }

    private func isRead(series: Series, index: Int) -> Bool {
        guard let lastRead = series.lastRead,
              let lastIndex = series.chapters.firstIndex(where: { $0.id == lastRead.id }) else { return false }
        return index >= lastIndex
    }

    private func modeBinding(_ series: Series) -> Binding<ReadingMode> {
        Binding(
            get: { model.series.first(where: { $0.id == series.id })?.readingMode ?? .vertical },
            set: { mode in Task { await model.setMode(seriesID: series.id, mode: mode) } }
        )
    }
}
