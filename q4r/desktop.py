"""Native Qt desktop shell around the bundled local interface."""
import argparse
import base64
import json
from pathlib import Path
import shutil
import sys
import threading


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--engine"]:
        from .cli import main as engine_main
        return engine_main(args[1:])
    if args[:1] == ["--playwright"]:
        import playwright.__main__
        sys.argv = ["playwright", *args[1:]]
        return playwright.__main__.main()
    parser = argparse.ArgumentParser(prog="q4r-studio")
    parser.add_argument("--case-root", type=Path)
    parser.add_argument("--smoke-exit", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-search", choices=("offline", "public"), help=argparse.SUPPRESS)
    parser.add_argument("--smoke-output", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args(args)
    try:
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QStandardPaths, Qt, QTimer, QUrl, Signal, Slot
        from PySide6.QtGui import QDesktopServices, QIcon, QImage, QImageReader, QPainter
        from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError as exc:
        print(f"Desktop runtime error: {exc}", file=sys.stderr)
        print("Install desktop support: pipx inject q4r-osint PySide6 PySide6-WebEngine", file=sys.stderr)
        return 2
    from .app_service import AppService
    from .core import safe_url

    app = QApplication([sys.argv[0]])
    app.setApplicationName("Q4R")
    app.setOrganizationName("Q4R")
    storage = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    service = AppService(options.case_root or storage/"cases")
    ui = Path(__file__).with_name("ui")/"index.html"

    class Bridge(QObject):
        response = Signal(str, str)

        def reply(self, request_id, data=None, error=None):
            self.response.emit(request_id, json.dumps(dict(ok=error is None, data=data, error=error), ensure_ascii=False))

        @Slot(str, str)
        def request(self, request_id, raw):
            try:
                value = json.loads(raw)
                action, payload = value["action"], value.get("payload") or {}
                if action == "choose_photo":
                    path, _ = QFileDialog.getOpenFileName(window, "Reference photo / الصورة المرجعية", "", "Images (*.png *.jpg *.jpeg *.webp)")
                    if not path:
                        self.reply(request_id)
                        return
                    reader = QImageReader(path)
                    reader.setAutoTransform(True)
                    size = reader.size()
                    if not size.isValid() or size.width()*size.height() > 45_000_000:
                        raise ValueError("Choose an image smaller than 45 megapixels")
                    if max(size.width(), size.height()) > 1400:
                        reader.setScaledSize(size.scaled(1400, 1400, Qt.KeepAspectRatio))
                    source = reader.read()
                    if source.isNull():
                        raise ValueError("This image could not be read")
                    # Copy pixels into a new image, discarding source metadata.
                    pixels = QImage(source.size(), QImage.Format_RGB32)
                    pixels.fill(Qt.white)
                    painter = QPainter(pixels)
                    painter.drawImage(0, 0, source)
                    painter.end()
                    output = QByteArray()
                    buffer = QBuffer(output)
                    buffer.open(QIODevice.WriteOnly)
                    if not pixels.save(buffer, "PNG"):
                        raise ValueError("Image conversion failed")
                    self.reply(request_id, dict(data_url="data:image/png;base64,"+base64.b64encode(bytes(output)).decode("ascii")))
                    return
                if action in ("open_url", "open_report"):
                    if action == "open_url":
                        url = QUrl(service.handle("open_url", payload))
                    else:
                        path = Path(service.handle("report_path", payload))
                        if not path.is_file():
                            raise ValueError("The report is not ready")
                        url = QUrl.fromLocalFile(str(path))
                    if not QDesktopServices.openUrl(url):
                        raise ValueError("No application could open this link")
                    self.reply(request_id, True)
                    return
                if action == "export":
                    filename, _ = QFileDialog.getSaveFileName(window, "Export case / تصدير الملف", str(Path.home()/(payload["case_id"]+".zip")), "ZIP case (*.zip)")
                    if not filename:
                        self.reply(request_id)
                        return
                    def export():
                        try:
                            generated = service.handle("export", payload)
                            if Path(generated).resolve() != Path(filename).resolve():
                                shutil.copyfile(generated, filename)
                            self.reply(request_id, dict(saved=True))
                        except Exception as exc:
                            self.reply(request_id, error=str(exc))
                    threading.Thread(target=export, daemon=True).start()
                    return
                def execute():
                    self.response.emit(request_id, service.request(raw))
                threading.Thread(target=execute, daemon=True).start()
            except Exception as exc:
                self.reply(request_id, error=str(exc))

    class LocalPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            if not is_main_frame:
                return False
            return url.isLocalFile() and Path(url.toLocalFile()).resolve() == ui.resolve()

    class Window(QMainWindow):
        def closeEvent(self, event):
            service.cancel.set()
            event.accept()

    window = Window()
    window.setWindowTitle("Q4R — Public-source research")
    window.setWindowIcon(QIcon(str(ui.with_name("mark.svg"))))
    window.resize(1240, 940)
    window.setMinimumSize(380, 640)
    view = QWebEngineView(window)
    profile = QWebEngineProfile("Q4R", view)
    profile.setPersistentStoragePath(str(storage/"ui-profile"))
    profile.setCachePath(str(storage/"ui-cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
    page = LocalPage(profile, view)
    view.setPage(page)
    page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    page.settings().setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
    channel = QWebChannel(page)
    bridge = Bridge(window)
    channel.registerObject("backend", bridge)
    page.setWebChannel(channel)
    view.setUrl(QUrl.fromLocalFile(str(ui)))
    window.setCentralWidget(view)
    window.show()
    if options.smoke_search:
        def begin_search():
            script = """(async()=>{for(let i=0;i<100;i++){if(document.getElementById('connectionNotice')?.hidden)break;await new Promise(r=>setTimeout(r,100));}document.getElementById('fullName').value='';document.getElementById('username').value='octocat';document.querySelectorAll('#sourceChoices input').forEach(n=>n.checked=n.value==='github');document.getElementById('offlineMode').checked=OFFLINE;document.getElementById('username').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('startButton').click();})()""".replace("OFFLINE", "true" if options.smoke_search == "offline" else "false")
            page.runJavaScript(script)
        QTimer.singleShot(500, begin_search)
    if options.smoke_exit:
        def finish_smoke():
            def checked(ready):
                if options.smoke_screenshot:
                    view.grab().save(str(options.smoke_screenshot))
                if options.smoke_search:
                    state = service.handle("status")
                    if options.smoke_output:
                        options.smoke_output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                    ready = ready and state["report"] is not None and state["status"] in ("planned", "complete", "partial")
                app.exit(0 if ready else 3)
            page.runJavaScript("document.getElementById('connectionNotice').hidden && document.querySelectorAll('#sourceChoices input').length>0", checked)
        QTimer.singleShot(options.smoke_exit, finish_smoke)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
