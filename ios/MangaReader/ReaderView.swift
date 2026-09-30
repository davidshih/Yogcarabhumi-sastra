import ReaderCore
import SwiftUI
import UIKit

struct ReaderView: View {
    @Bindable var model: AppModel
    let seriesID: String
    let initialChapter: Chapter

    @Environment(\.dismiss) private var dismiss
    @State private var chapter: Chapter
    @State private var pages: [URL] = []
    @State private var mode: ReadingMode
    @State private var currentPage: Int?
    @State private var overlayVisible = false
    @State private var loading = true
    @State private var loadError: String?
    @State private var finished: Set<String> = []
    @State private var hideOverlayTask: Task<Void, Never>?
    @State private var hasFlashedOverlay = false
    @State private var dragOffset: CGFloat = 0
    @State private var atTop = true
    @State private var atBottom = false
    @State private var nextPull: CGFloat = 0
    @State private var isZoomed = false

    init(model: AppModel, seriesID: String, initialChapter: Chapter) {
        self.model = model
        self.seriesID = seriesID
        self.initialChapter = initialChapter
        _chapter = State(initialValue: initialChapter)
        let initialMode = model.series.first(where: { $0.id == seriesID })?.readingMode ?? .vertical
        _mode = State(initialValue: initialMode)
    }

    private var series: Series? {
        model.series.first(where: { $0.id == seriesID })
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            Group {
            if loading {
                VStack(spacing: 12) {
                    ProgressView().tint(.white)
                    if let progress = model.downloads[chapter.id] {
                        Text("下載中 \(progress.done)/\(progress.total)")
                            .foregroundStyle(.white)
                    } else {
                        Text("正在開啟章節…").foregroundStyle(.white)
                    }
                }
            } else if let loadError {
                VStack(spacing: 16) {
                    Image(systemName: "wifi.exclamationmark")
                        .font(.largeTitle)
                    Text("無法下載").font(.title2.bold())
                    Text(loadError)
                        .font(.footnote)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                    Button("重試") { Task { await loadChapter() } }
                        .buttonStyle(.borderedProminent)
                }
                .padding(32)
                .foregroundStyle(.white)
            } else if mode == .vertical {
                verticalReader
            } else {
                pagedReader
            }
            }
            .offset(y: dragOffset)
            .simultaneousGesture(pullDrag)

            if overlayVisible && !loading {
                readerOverlay
                    .transition(.opacity)
            } else {
                floatingCloseButton
            }
        }
        .preferredColorScheme(.dark)
        .statusBarHidden(!overlayVisible)
        .task { await loadChapter() }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
            Task { await model.flushResumeSaves() }
        }
        .onChange(of: currentPage) { _, page in
            guard let page, page >= 0, page < pages.count else { return }
            model.setResumePage(seriesID: seriesID, chapterID: chapter.id, page: page)
            if mode == .pagedRTL, page == pages.count - 1 {
                finishChapter()
            }
        }
    }

    private var verticalReader: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(Array(pages.enumerated()), id: \.offset) { index, url in
                    PageImageView(url: url, maxPixelSize: 1170) {
                        Task { await redownloadChapter() }
                    }
                    .containerRelativeFrame(.horizontal)
                    .id(index)
                }
                endCard
                    .frame(maxWidth: .infinity, minHeight: 320)
                    .background(Color.black)
                    .onScrollVisibilityChange(threshold: 0.5) { visible in
                        if visible { finishChapter() }
                    }
            }
            .scrollTargetLayout()
        }
        .scrollPosition(id: $currentPage)
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top <= 1
        } action: { _, isAtTop in
            atTop = isAtTop
        }
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.containerSize.height
                >= geometry.contentSize.height + geometry.contentInsets.bottom - 1
        } action: { _, isAtBottom in
            atBottom = isAtBottom
        }
        .onTapGesture { toggleOverlay() }
    }

    private var pagedReader: some View {
        TabView(selection: pageSelection) {
            ForEach(Array(pages.enumerated()), id: \.offset) { index, url in
                ZoomablePage(url: url) { x in
                    if x < 1 / 3 {
                        currentPage = min(index + 1, pages.count)
                    } else if x > 2 / 3 {
                        currentPage = max(index - 1, 0)
                    } else {
                        toggleOverlay()
                    }
                } zoomChanged: { zoomed in
                    isZoomed = zoomed
                } corruptAction: {
                    Task { await redownloadChapter() }
                }
                .tag(index)
            }
            endCard
                .tag(pages.count)
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
        .environment(\.layoutDirection, .rightToLeft)
    }

    /// Always-on escape hatch while the toolbar is hidden: small and translucent, inside the safe area below the notch.
    private var floatingCloseButton: some View {
        VStack {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .background(.ultraThinMaterial, in: Circle())
                }
                .opacity(0.55)
                .accessibilityLabel("關閉閱讀器")
                Spacer()
            }
            Spacer()
        }
        .padding(.leading, 12)
        .padding(.top, 4)
        .foregroundStyle(.white)
    }

    /// Two pulls, each only where it cannot fight another gesture:
    /// down to close, like Photos (vertical mode at the top of the chapter, paged mode while not zoomed);
    /// up past the end of the chapter to open the next one (vertical mode, scrolled to the bottom).
    private var pullDrag: some Gesture {
        DragGesture(minimumDistance: 20)
            .onChanged { value in
                let vertical = abs(value.translation.height) > abs(value.translation.width)
                guard vertical else { return }
                if value.translation.height > 0, canPullToDismiss {
                    dragOffset = value.translation.height
                } else if value.translation.height < 0, canPullToNext {
                    nextPull = -value.translation.height
                }
            }
            .onEnded { value in
                let t = value.translation, end = value.predictedEndTranslation
                if canPullToDismiss && pullCommits(distance: t.height, predicted: end.height, sideways: t.width) {
                    dismiss()
                } else if canPullToNext, let nextChapter,
                          pullCommits(distance: -t.height, predicted: -end.height, sideways: t.width) {
                    nextPull = 0
                    switchChapter(to: nextChapter)
                } else {
                    withAnimation(.spring(duration: 0.3)) {
                        dragOffset = 0
                        nextPull = 0
                    }
                }
            }
    }

    private var canPullToDismiss: Bool {
        if loading || loadError != nil { return true }
        return mode == .vertical ? atTop : !isZoomed
    }

    private var canPullToNext: Bool {
        mode == .vertical && !loading && loadError == nil && atBottom && nextChapter != nil
    }

    /// Whether a pull, measured along its own direction, was deliberate when the finger lifts.
    /// `distance` is how far the finger moved; `predicted` is where a flick would carry it.
    private func pullCommits(distance: CGFloat, predicted: CGFloat, sideways: CGFloat) -> Bool {
        // Mostly sideways means a page turn in paged mode, never a close or chapter change.
        guard distance > abs(sideways) else { return false }
        // A slow pull past ~1/7 of the 844 pt screen, or a quick flick that would carry past 300 pt.
        return distance > pullThreshold || predicted > 300
    }

    private let pullThreshold: CGFloat = 120

    private func toggleOverlay() {
        hideOverlayTask?.cancel()
        withAnimation(.easeInOut(duration: 0.2)) { overlayVisible.toggle() }
    }

    /// Shows the toolbar for 2 s on open so the tap-to-show gesture is discoverable.
    private func flashOverlayOnce() {
        guard !hasFlashedOverlay else { return }
        hasFlashedOverlay = true
        withAnimation(.easeInOut(duration: 0.2)) { overlayVisible = true }
        hideOverlayTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation(.easeInOut(duration: 0.4)) { overlayVisible = false }
        }
    }

    private var readerOverlay: some View {
        VStack {
            HStack(spacing: 12) {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .frame(width: 44, height: 44)
                        .background(.ultraThinMaterial, in: Circle())
                }
                .accessibilityLabel("關閉閱讀器")
                VStack(alignment: .leading) {
                    Text(series?.name ?? "").font(.headline).lineLimit(1)
                    Text(chapter.label).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Picker("閱讀模式", selection: modeBinding) {
                    Text("直").tag(ReadingMode.vertical)
                    Text("翻").tag(ReadingMode.pagedRTL)
                }
                .pickerStyle(.segmented)
                .frame(width: 110)
            }
            .padding(.horizontal, 12)
            .padding(.top, 8)
            .background(.black.opacity(0.78))

            Spacer()

            VStack(spacing: 10) {
                if !pages.isEmpty {
                    HStack {
                        Text("\(min((currentPage ?? 0) + 1, pages.count)) / \(pages.count)")
                            .font(.caption.monospacedDigit())
                        Slider(value: sliderValue, in: 0...Double(max(pages.count - 1, 1)), step: 1)
                    }
                }
                HStack {
                    Button("上一話", systemImage: "chevron.left") {
                        if let previousChapter { switchChapter(to: previousChapter) }
                    }
                    .disabled(previousChapter == nil)
                    Spacer()
                    Button("下一話", systemImage: "chevron.right") {
                        if let nextChapter { switchChapter(to: nextChapter) }
                    }
                    .disabled(nextChapter == nil)
                }
                .labelStyle(.titleAndIcon)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
            .background(.black.opacity(0.78))
        }
        .foregroundStyle(.white)
    }

    private var endCard: some View {
        VStack(spacing: 20) {
            Image(systemName: nextChapter == nil ? "checkmark.circle.fill" : "arrow.right.circle.fill")
                .font(.system(size: 44))
            if let nextChapter {
                Button("下一話：\(nextChapter.label) →") {
                    switchChapter(to: nextChapter)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                if mode == .vertical {
                    Text(nextPull > pullThreshold ? "放開，看下一話" : "繼續往上拉，看下一話")
                        .font(.footnote)
                        .foregroundStyle(nextPull > pullThreshold ? .white : .gray)
                }
            } else {
                Text("已追上最新").font(.title2.bold())
                Button("關閉") { dismiss() }
                    .buttonStyle(.bordered)
            }
        }
        .foregroundStyle(.white)
        .environment(\.layoutDirection, .leftToRight)
    }

    private var modeBinding: Binding<ReadingMode> {
        Binding(
            get: { mode },
            set: { newMode in
                mode = newMode
                currentPage = min(currentPage ?? 0, max(pages.count - 1, 0))
                Task { await model.setMode(seriesID: seriesID, mode: newMode) }
            }
        )
    }

    private var pageSelection: Binding<Int> {
        Binding(
            get: { currentPage ?? 0 },
            set: { currentPage = $0 }
        )
    }

    private var sliderValue: Binding<Double> {
        Binding(
            get: { Double(currentPage ?? 0) },
            set: { currentPage = Int($0.rounded()) }
        )
    }

    private var currentChapterIndex: Int? {
        series?.chapters.firstIndex(where: { $0.id == chapter.id })
    }

    private var nextChapter: Chapter? {
        guard let series, let index = currentChapterIndex, index > 0 else { return nil }
        return series.chapters[index - 1]
    }

    private var previousChapter: Chapter? {
        guard let series, let index = currentChapterIndex, index + 1 < series.chapters.count else { return nil }
        return series.chapters[index + 1]
    }

    private func finishChapter() {
        guard finished.insert(chapter.id).inserted else { return }
        Task { await model.markFinished(seriesID: seriesID, chapter: chapter) }
    }

    private func switchChapter(to next: Chapter) {
        chapter = next
        pages = []
        currentPage = nil
        loadError = nil
        loading = true
        Task { await loadChapter() }
    }

    private func loadChapter() async {
        loading = true
        loadError = nil
        do {
            pages = try await model.pages(for: chapter)
            let resume = series?.resumePage[chapter.id] ?? 0
            currentPage = min(max(resume, 0), max(pages.count - 1, 0))
            loading = false
            flashOverlayOnce()
        } catch {
            loadError = error.localizedDescription
        }
        loading = false
    }

    private func redownloadChapter() async {
        await model.removeCachedChapter(chapter.id)
        await loadChapter()
    }
}

private struct ZoomablePage: View {
    let url: URL
    let tapAction: (CGFloat) -> Void
    let zoomChanged: (Bool) -> Void
    let corruptAction: () -> Void

    @State private var scale: CGFloat = 1
    @State private var settledScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var settledOffset: CGSize = .zero

    var body: some View {
        GeometryReader { proxy in
            PageImageView(url: url, maxPixelSize: scale > 1 ? 2340 : 1170, corruptAction: corruptAction)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .scaleEffect(scale)
                .offset(offset)
                .contentShape(Rectangle())
                .gesture(
                    MagnifyGesture()
                        .onChanged { value in
                            scale = min(max(settledScale * value.magnification, 1), 4)
                            if scale == 1 { offset = .zero }
                        }
                        .onEnded { _ in
                            settledScale = scale
                            zoomChanged(scale > 1)
                            if scale == 1 {
                                offset = .zero
                                settledOffset = .zero
                            }
                        }
                )
                .simultaneousGesture(
                    DragGesture()
                        .onChanged { value in
                            guard scale > 1 else { return }
                            offset = CGSize(
                                width: settledOffset.width + value.translation.width,
                                height: settledOffset.height + value.translation.height
                            )
                        }
                        .onEnded { _ in settledOffset = offset }
                )
                .simultaneousGesture(
                    SpatialTapGesture()
                        .onEnded { value in
                            guard scale == 1 else { return }
                            tapAction(value.location.x / max(proxy.size.width, 1))
                        }
                )
                .onTapGesture(count: 2) {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        scale = scale == 1 ? 2.5 : 1
                        settledScale = scale
                        zoomChanged(scale > 1)
                        if scale == 1 {
                            offset = .zero
                            settledOffset = .zero
                        }
                    }
                }
        }
    }
}
