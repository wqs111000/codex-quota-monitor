import Cocoa
import Foundation

private struct QuotaWindow: Decodable {
    let name: String?
    let remainingPercent: Double?
    let resetAt: String?
    let hoursToReset: Double?

    enum CodingKeys: String, CodingKey {
        case name
        case remainingPercent = "remaining_percent"
        case resetAt = "reset_at"
        case hoursToReset = "hours_to_reset"
    }
}

private struct Dashboard: Decodable {
    let windows: [QuotaWindow]
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var windows: [QuotaWindow] = []
    private var lastSync: Date?
    private var timer: Timer?
    private var serviceProcess: Process?
    private var ownsService = false
    private var isQuitting = false
    private let endpoint = URL(string: "http://127.0.0.1:5077/api/status")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
        statusItem.button?.toolTip = "Codex 剩余额度"

        menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        refreshMenu()
        ensureService()
        fetchQuota()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.fetchQuota()
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        refreshMenu()
    }

    @objc private func refreshNow() {
        fetchQuota()
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:5077/")!)
    }

    @objc private func quit() {
        isQuitting = true
        stopService()
        NSApp.terminate(nil)
    }

    private var projectRoot: URL {
        URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func ensureService() {
        URLSession.shared.dataTask(with: endpoint) { [weak self] _, response, _ in
            guard let self else { return }
            guard !(response is HTTPURLResponse) else { return }
            DispatchQueue.main.async { self.startService() }
        }.resume()
    }

    private func startService() {
        guard serviceProcess == nil else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", projectRoot.appendingPathComponent("app.py").path]
        process.currentDirectoryURL = projectRoot
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.serviceProcess = nil
                self.ownsService = false
                if !self.isQuitting {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.ensureService() }
                }
            }
        }
        do {
            try process.run()
            serviceProcess = process
            ownsService = true
        } catch {
            serviceProcess = nil
        }
    }

    private func stopService() {
        guard ownsService, let process = serviceProcess, process.isRunning else { return }
        process.terminate()
        serviceProcess = nil
        ownsService = false
    }

    private func fetchQuota() {
        URLSession.shared.dataTask(with: endpoint) { [weak self] data, response, _ in
            guard let self, let data, let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                DispatchQueue.main.async { self?.showUnavailable() }
                return
            }
            do {
                let dashboard = try JSONDecoder().decode(Dashboard.self, from: data)
                DispatchQueue.main.async {
                    self.windows = dashboard.windows
                    self.lastSync = Date()
                    self.refreshMenu()
                }
            } catch {
                DispatchQueue.main.async { self.showUnavailable() }
            }
        }.resume()
    }

    private func showUnavailable() {
        statusItem.button?.title = "—"
        statusItem.button?.toolTip = "额度服务未连接"
    }

    private func refreshMenu() {
        guard let button = statusItem?.button else { return }
        if let primary = windows.min(by: { ($0.remainingPercent ?? 101) < ($1.remainingPercent ?? 101) }), let remaining = primary.remainingPercent {
            button.title = "\(Int(round(remaining)))%"
            button.toolTip = "Codex · 剩余 \(Int(round(remaining)))%"
        } else {
            button.title = "—"
        }

        menu?.removeAllItems()
        let header = NSMenuItem(title: "CODEX QUOTA", action: nil, keyEquivalent: "")
        header.attributedTitle = NSAttributedString(string: "CODEX QUOTA", attributes: [.font: NSFont.systemFont(ofSize: 11, weight: .bold), .foregroundColor: NSColor.secondaryLabelColor])
        menu.addItem(header)
        menu.addItem(.separator())

        if windows.isEmpty {
            menu.addItem(NSMenuItem(title: "暂无额度数据", action: nil, keyEquivalent: ""))
        } else {
            for window in windows {
                let remaining = window.remainingPercent.map { "\(Int(round($0)))%" } ?? "—"
                let reset = window.hoursToReset.map(formatDuration) ?? "—"
                let item = NSMenuItem(title: "\(window.name ?? "额度")    剩余 \(remaining)", action: nil, keyEquivalent: "")
                item.toolTip = "距离重置 \(reset)"
                menu.addItem(item)
                let detail = NSMenuItem(title: "    重置倒计时  \(reset)", action: nil, keyEquivalent: "")
                detail.isEnabled = false
                menu.addItem(detail)
            }
        }

        menu.addItem(.separator())
        let syncText = lastSync.map { "更新于 \(timeString($0))" } ?? "等待本地服务"
        let sync = NSMenuItem(title: syncText, action: nil, keyEquivalent: "")
        sync.isEnabled = false
        menu.addItem(sync)
        menu.addItem(NSMenuItem(title: "立即刷新", action: #selector(refreshNow), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "打开额度仪表盘", action: #selector(openDashboard), keyEquivalent: "o"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出额度监控", action: #selector(quit), keyEquivalent: "q"))
    }

    private func formatDuration(_ hours: Double) -> String {
        let minutes = max(0, Int(round(hours * 60)))
        let days = minutes / 1440
        let restHours = (minutes % 1440) / 60
        let restMinutes = minutes % 60
        if days > 0 { return "\(days) 天\(restHours > 0 ? " \(restHours) 小时" : "")" }
        if restHours > 0 { return "\(restHours) 小时\(restMinutes > 0 ? " \(restMinutes) 分钟" : "")" }
        return "\(restMinutes) 分钟"
    }

    private func timeString(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
