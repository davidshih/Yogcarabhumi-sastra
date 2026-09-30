import BackgroundTasks
import SwiftUI

@main
struct MangaReaderApp: App {
    private static let backgroundTaskID = "com.davidshih.MangaReader.sync"

    @Environment(\.scenePhase) private var scenePhase
    @State private var model: AppModel

    init() {
        let model = AppModel()
        _model = State(initialValue: model)
        BGTaskScheduler.shared.register(forTaskWithIdentifier: "com.davidshih.MangaReader.sync", using: nil) { task in
            guard let processingTask = task as? BGProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            let work = Task { @MainActor in
                await model.start()
                await model.sync()
                processingTask.setTaskCompleted(success: !Task.isCancelled)
                Self.scheduleBackgroundSync()
            }
            processingTask.expirationHandler = { work.cancel() }
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView(model: model)
                .tint(Theme.red)
                .task { await model.start() }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                Task { @MainActor in
                    await model.start()
                    if model.needsAutoSync { await model.sync() }
                }
            case .background:
                Task { @MainActor in await model.flushResumeSaves() }
                Self.scheduleBackgroundSync()
            default:
                break
            }
        }
    }

    private static func scheduleBackgroundSync() {
        let request = BGProcessingTaskRequest(identifier: backgroundTaskID)
        request.requiresNetworkConnectivity = true
        request.earliestBeginDate = Date(timeIntervalSinceNow: 4 * 60 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }
}

private struct RootView: View {
    let model: AppModel

    var body: some View {
        switch model.phase {
        case .starting:
            ProgressView("正在載入追更簿…")
        case .ready:
            LibraryView(model: model)
        case .failed(let message):
            ContentUnavailableView(
                "無法載入追更簿",
                systemImage: "exclamationmark.triangle",
                description: Text(message)
            )
        }
    }
}
