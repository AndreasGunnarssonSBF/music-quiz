#!/usr/bin/env python3
"""Musikquiz – startprogram.

Dubbelklick på appen:
  * Kör Musikquiz redan? Då öppnas bara webbläsaren.
  * Annars startas servern som en egen bakgrundsprocess och webbläsaren öppnas.
Själva appen avslutas direkt, så att ett nytt dubbelklick alltid fungerar (macOS startar
annars inte om en app som redan körs). Servern stänger sig själv när man trycker
Avsluta i webbläsaren, eller när ingen Musikquiz-sida varit öppen på 15 minuter.

  app.py            starta (som ett dubbelklick)
  app.py --server   kör servern i den här processen (används av startprogrammet)
  app.py --dev      kör servern i förgrunden och öppna webbläsaren (för utveckling)
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

ADMIN_PORTS = range(8765, 8775)


def running_port():
    for port in ADMIN_PORTS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.4) as r:
                if json.load(r).get("app") == "musikquiz":
                    return port
        except Exception:
            continue
    return None


def open_browser(port):
    url = f"http://127.0.0.1:{port}/"
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        webbrowser.open(url)


def log_path():
    from library import home_dir
    d = os.path.join(home_dir(), ".state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "musikquiz.log")


def run_server(open_after=False):
    from server import App
    app = App()
    port = app.start(ADMIN_PORTS)
    print(f"Musikquiz körs: http://127.0.0.1:{port}/  (data i {app.lib.root})", flush=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: app.shutdown())
        except (ValueError, OSError):
            pass
    if open_after:
        open_browser(port)
    app.wait()


def launch():
    port = running_port()
    if port:
        open_browser(port)
        return
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--server"]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), "--server"]
    log = open(log_path(), "a", encoding="utf-8")
    kw = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)
    for _ in range(100):
        time.sleep(0.15)
        port = running_port()
        if port:
            open_browser(port)
            return
    # Servern kom inte igång – visa loggen så att det går att se varför.
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-e", log_path()])
    elif os.name == "nt":
        os.startfile(log_path())  # noqa


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if "--server" in sys.argv:
        run_server()
    elif "--dev" in sys.argv:
        run_server(open_after="--no-browser" not in sys.argv)
    else:
        launch()


if __name__ == "__main__":
    main()
