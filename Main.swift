import AppKit
import WebKit
import UniformTypeIdentifiers
import Darwin

final class DropWebView: WKWebView {
    var onFile: ((URL) -> Void)?
    var acceptsFiles = true
    func files(_ sender: NSDraggingInfo) -> [URL] {
        sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        let urls = files(sender)
        let allowed = acceptsFiles && urls.count == 1 && urls[0].pathExtension.lowercased() == "ipa"
        evaluateJavaScript("document.body.classList.toggle('dragging', \(allowed))", completionHandler: nil)
        return allowed ? .copy : []
    }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { draggingEntered(sender) }
    override func draggingExited(_ sender: NSDraggingInfo?) {
        evaluateJavaScript("document.body.classList.remove('dragging')", completionHandler: nil)
    }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { acceptsFiles }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        draggingExited(sender)
        let urls = files(sender)
        guard acceptsFiles, urls.count == 1, urls[0].pathExtension.lowercased() == "ipa" else { return false }
        onFile?(urls[0]); return true
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    var window: NSWindow!
    var web: DropWebView!
    var ready = false
    var queued = [[String: Any]]()
    var selected: URL?
    var pending: URL?
    var output: URL?
    var validated = false
    var flavor = "vitrine"
    var process: Process?
    var operation = ""
    var cancelled = false
    var terminalEvent = false
    var autoPatch = false
    var checks = [Process]()
    var snapshotPath: String?
    var testReport: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.appearance = NSAppearance(named: .darkAqua)
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Spotify Patcher", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Spotify Patcher", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu; menu.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit; menu.addItem(editItem)
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 800), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Spotify Patcher"
        window.minSize = NSSize(width: 760, height: 650)
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.backgroundColor = NSColor(calibratedWhite: 0.078, alpha: 1)
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "patcher")
        web = DropWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.registerForDraggedTypes([.fileURL])
        web.onFile = { [weak self] url in self?.select(url) }
        window.contentView = web
        let args = CommandLine.arguments
        func argument(_ key: String) -> String? {
            guard let i = args.firstIndex(of: key), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        if argument("--flavor") == "chroma" { flavor = "chroma" }
        if let path = argument("--patch") { pending = URL(fileURLWithPath: path); autoPatch = true }
        if let path = argument("--select") { pending = URL(fileURLWithPath: path) }
        snapshotPath = argument("--snapshot")
        testReport = argument("--test-report")
        let resources = Bundle.main.resourceURL!
        web.loadFileURL(resources.appendingPathComponent("index.html"), allowingReadAccessTo: resources)
        window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        ready = true
        // Route Finder drops to the native file handler instead of WebKit navigation.
        func unregisterChildDrops(_ view: NSView) {
            for child in view.subviews { child.unregisterDraggedTypes(); unregisterChildDrops(child) }
        }
        unregisterChildDrops(web)
        web.registerForDraggedTypes([.fileURL])
        send(["type": "flavor", "flavor": flavor])
        for event in queued { send(event) }; queued.removeAll()
        checkVersions()
        if let url = pending { pending = nil; select(url) }
        if CommandLine.arguments.contains("--about") { showAbout() }
        if CommandLine.arguments.contains("--tutorial") { web.evaluateJavaScript("showPage('tutorial')", completionHandler: nil) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.capture() }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.request.url?.isFileURL == true && navigationAction.navigationType == .other { decisionHandler(.allow) }
        else { decisionHandler(.cancel) }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.isFileURL == true,
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "choose": choose()
        case "flavor":
            if process == nil, let value = body["value"] as? String, ["vitrine", "chroma"].contains(value) {
                flavor = value; output = nil
                send(["type": "flavor", "flavor": value])
            }
        case "patch": if process == nil, validated, let url = selected { output = nil; launch(["build", url.path, "--flavor", flavor]) }
        case "cancel": cancel()
        case "reveal": if let url = output { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        case "refresh": if process == nil { checkVersions() }
        case "external":
            if let value = body["url"] as? String, let url = URL(string: value), url.scheme == "https",
               ["github.com", "chroma.pw", "armconverter.com", "iosgods.com", "sideloadly.io", "support.spotify.com", "community.spotify.com"].contains(url.host ?? "") { NSWorkspace.shared.open(url) }
        default: break
        }
    }
    func send(_ event: [String: Any]) {
        guard ready else { queued.append(event); return }
        guard let data = try? JSONSerialization.data(withJSONObject: event), let json = String(data: data, encoding: .utf8) else { return }
        web.evaluateJavaScript("window.receive(\(json))", completionHandler: nil)
    }
    @objc func showAbout() { web?.evaluateJavaScript("showPage('about')", completionHandler: nil) }
    func choose() {
        guard process == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose your decrypted Spotify IPA"
        panel.allowedContentTypes = [UTType(filenameExtension: "ipa") ?? .data]
        panel.allowsMultipleSelection = false; panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { [weak self] response in
            if response == .OK, let url = panel.url { self?.select(url) }
        }
    }
    func select(_ url: URL) {
        guard process == nil else { NSSound.beep(); return }
        selected = url; validated = false; output = nil
        send(["type": "selected", "name": url.lastPathComponent])
        launch(["validate", url.path])
    }
    func checkVersions() {
        guard checks.isEmpty else { return }
        send(["type": "checking"])
        for choice in ["vitrine", "chroma"] {
            let p = Process(); let pipe = Pipe()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            p.arguments = ["-B", "-u", Bundle.main.path(forResource: "backend", ofType: "py")!, "check", "--flavor", choice]
            p.standardOutput = pipe; p.standardError = pipe
            checks.append(p)
            // Version output is tiny; drain it in the background before waiting for exit.
            do {
                try p.run()
                DispatchQueue.global().async {
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    p.waitUntilExit()
                    DispatchQueue.main.async {
                        for line in data.split(separator: 10) {
                            if var event = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] {
                                if event["type"] as? String == "error" { event["type"] = "versionError"; event["flavor"] = choice }
                                self.send(event)
                            }
                        }
                        self.checks.removeAll { $0 === p }
                        if self.checks.isEmpty { self.send(["type": "checked"]) }
                    }
                }
            } catch {
                checks.removeAll { $0 === p }
                send(["type": "versionError", "flavor": choice])
                if checks.isEmpty { send(["type": "checked"]) }
            }
        }
    }
    func launch(_ arguments: [String]) {
        let p = Process(); let out = Pipe()
        operation = arguments[0]; cancelled = false; terminalEvent = false
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-B", "-u", Bundle.main.path(forResource: "backend", ofType: "py")!] + arguments
        p.standardOutput = out; p.standardError = out
        process = p; web.acceptsFiles = false
        send(["type": "busy", "operation": operation])
        do {
            try p.run()
            DispatchQueue.global().async {
                var buffer = Data()
                while true {
                    let data = out.fileHandleForReading.availableData
                    if data.isEmpty { break }
                    buffer.append(data)
                    while let newline = buffer.firstIndex(of: 10) {
                        let line = Data(buffer[..<newline]); buffer.removeSubrange(...newline)
                        DispatchQueue.main.async { self.handleLine(line) }
                    }
                }
                if !buffer.isEmpty { let last = buffer; DispatchQueue.main.async { self.handleLine(last) } }
                p.waitUntilExit()
                DispatchQueue.main.async {
                    self.process = nil; self.web.acceptsFiles = true
                    if !self.terminalEvent { self.send(["type": "error", "message": self.cancelled ? "Patch cancelled." : "The patch stopped unexpectedly. Please try again."]) }
                    self.send(["type": "idle"])
                    if self.operation == "build" { DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.capture() } }
                    if self.autoPatch && self.validated && self.operation == "validate", self.selected != nil {
                        self.autoPatch = false; self.web.evaluateJavaScript("document.getElementById('patch').click()", completionHandler: nil)
                    }
                }
            }
        } catch {
            process = nil; web.acceptsFiles = true
            send(["type": "error", "message": "Could not start the patcher: \(error.localizedDescription)"])
            send(["type": "idle"])
        }
    }
    func handleLine(_ data: Data) {
        guard var event = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let type = event["type"] as? String else { return }
        if type == "log" { return }
        if type == "validated" { validated = true; terminalEvent = true }
        if type == "done" { terminalEvent = true; if let path = event["path"] as? String { output = URL(fileURLWithPath: path) }; NSSound(named: "Glass")?.play() }
        if type == "error" || type == "cancelled" {
            terminalEvent = true
            // Keep diagnostics on disk. The interface shows a short explanation only.
            if let message = event["message"] as? String { event["message"] = String(message.split(separator: "\n").first ?? "Could not finish patching.") }
        }
        send(event)
    }
    func cancel() {
        guard let p = process else { return }
        cancelled = true; autoPatch = false
        if kill(-p.processIdentifier, SIGTERM) != 0 { p.terminate() }
    }
    func capture() {
        if let path = snapshotPath {
            web.takeSnapshot(with: nil) { image, _ in
                if let data = image?.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data) {
                    try? bitmap.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
                }
            }
        }
        if let path = testReport {
            web.evaluateJavaScript("JSON.stringify({title:document.title,selected:document.getElementById('fileName').textContent,patchEnabled:!document.getElementById('patch').disabled,flavor:document.getElementById('flavor').value,aboutRows:document.querySelectorAll('tbody tr').length,logoLoaded:document.querySelector('#modLogo').complete,visibleTerminal:!!document.querySelector('textarea,pre'),status:document.getElementById('status').textContent,resultVisible:!document.getElementById('reveal').classList.contains('hidden'),scrollY:window.scrollY})") { result, _ in
                if let value = result as? String { try? value.write(toFile: path, atomically: true, encoding: .utf8) }
            }
        }
    }
    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        guard filenames.count == 1, let name = filenames.first, URL(fileURLWithPath: name).pathExtension.lowercased() == "ipa" else { sender.reply(toOpenOrPrint: .failure); return }
        let url = URL(fileURLWithPath: name)
        if !ready { pending = url } else { select(url) }
        sender.reply(toOpenOrPrint: .success)
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if process != nil && operation == "build" {
            let alert = NSAlert(); alert.messageText = "Stop patching and quit?"
            alert.informativeText = "Your original IPA will stay unchanged."
            alert.addButton(withTitle: "Keep patching"); alert.addButton(withTitle: "Stop and quit")
            if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        }
        cancel(); for p in checks where p.isRunning { p.terminate() }
        return .terminateNow
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
