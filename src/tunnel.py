"""Tillfällig internetadress via Cloudflare Quick Tunnel (trycloudflare.com).

Kräver inget konto. cloudflared kopplar upp sig utåt mot Cloudflare och skickar
trafiken vidare till den publika porten på den här datorn, så inga brandväggsregler
behövs. Bara mobilernas små svarsanrop går genom tunneln – aldrig media.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
START_TIMEOUT = 60


def resource_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def find_cloudflared():
    exe = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    bundled = os.path.join(resource_dir(), "bin", exe)
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which("cloudflared")
    if found:
        return found
    for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
        if os.path.isfile(p):
            return p
    return None


class Tunnel:
    def __init__(self, port, state_dir, on_change=lambda: None):
        self.port = port
        self.state_dir = state_dir
        self.on_change = on_change
        self.status = "off"        # off | starting | ready | failed | missing
        self.url = ""
        self.error = ""
        self.proc = None
        self.lock = threading.Lock()
        self.pid_file = os.path.join(state_dir, "cloudflared.pid")
        # Tom konfigfil: en eventuell ~/.cloudflared/config.yml hos användaren får inte styra snabbtunneln.
        self.empty_config = os.path.join(tempfile.gettempdir(), "musikquiz-cloudflared.yml")

    def info(self):
        return {"status": self.status, "url": self.url, "error": self.error}

    def _set(self, status, url=None, error=None):
        self.status = status
        if url is not None:
            self.url = url
        if error is not None:
            self.error = error
        try:
            self.on_change()
        except Exception:
            pass

    def kill_stale(self):
        """Stoppar en cloudflared som en tidigare körning lämnat kvar (om appen dödades hårt)."""
        try:
            with open(self.pid_file) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return
        try:
            if os.name == "nt":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                     capture_output=True, text=True, creationflags=0x08000000).stdout
                if "cloudflared" in out.lower():
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=0x08000000)
            else:
                out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout
                if "cloudflared" in out:
                    os.kill(pid, 15)
        except Exception:
            pass
        try:
            os.remove(self.pid_file)
        except OSError:
            pass

    def start(self):
        with self.lock:
            self.stop_locked()
            exe = find_cloudflared()
            if not exe:
                self._set("missing", "", "cloudflared hittades inte – lagen kan bara ansluta via wifi.")
                return
            try:
                with open(self.empty_config, "w") as f:
                    f.write("")
            except OSError:
                pass
            cmd = [exe, "tunnel", "--no-autoupdate", "--config", self.empty_config,
                   "--url", f"http://127.0.0.1:{self.port}"]
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, **kw)
            except OSError as e:
                self._set("failed", "", f"Kunde inte starta cloudflared: {e}")
                return
            try:
                with open(self.pid_file, "w") as f:
                    f.write(str(self.proc.pid))
            except OSError:
                pass
            self._set("starting", "", "")
            proc = self.proc
            threading.Thread(target=self._read, args=(proc,), daemon=True).start()
            threading.Thread(target=self._watchdog, args=(proc,), daemon=True).start()

    def _read(self, proc):
        url = ""
        tail = []
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            tail = (tail + [line])[-6:]
            if proc is not self.proc:
                continue
            m = URL_RE.search(line)
            if m and not url:
                url = m.group(0)
                self.url = url
            if url and "Registered tunnel connection" in line and self.status != "ready":
                self._set("ready", url, "")
        if proc is self.proc:
            msg = next((l for l in reversed(tail) if "ERR" in l or "error" in l.lower()), tail[-1] if tail else "")
            self._set("failed", "", "Tunneln stängdes. " + re.sub(r"^\S+\s+(ERR|INF|WRN)\s+", "", msg)[:200])

    def _watchdog(self, proc):
        t0 = time.monotonic()
        while time.monotonic() - t0 < START_TIMEOUT:
            if proc is not self.proc or self.status != "starting":
                return
            time.sleep(0.5)
        if proc is self.proc and self.status == "starting":
            self._set("failed", "", "Fick ingen kontakt med Cloudflare. Nätverket kan blockera tunneln – använd wifi-adressen.")
            try:
                proc.terminate()
            except OSError:
                pass

    def stop_locked(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        try:
            os.remove(self.pid_file)
        except OSError:
            pass
        self.status, self.url = "off", ""

    def stop(self):
        with self.lock:
            self.stop_locked()
