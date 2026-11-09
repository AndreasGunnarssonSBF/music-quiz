"""Musikquiz – server för den paketerade appen.

Två lyssnare:
  * Värdporten (127.0.0.1) – bibliotek, redigering, storskärm och all media. Nås bara från den här datorn.
  * Publika porten – bara det lagen behöver (mobilsidan och svars-API:t). Cloudflare-tunneln och
    (om quizmastern slår på det) wifi-läget pekar hit. Media går aldrig ut den vägen.
Bara standardbiblioteket. Mobilerna hämtar läget med long polling.
"""
import hmac
import json
import mimetypes
import os
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import library
from library import Library, check, prepare
from tunnel import Tunnel, resource_dir

APP_VERSION = "2.1"
STATIC_DIR = os.path.join(resource_dir(), "static")
MAX_TEAMS = 80
POLL_TIMEOUT = 25
IDLE_QUIT = 15 * 60          # sekunder utan någon öppen värdsida innan appen stänger sig själv
MAX_QUIZ_JSON = 5 * 1024 * 1024
MAX_UPLOAD = 4 * 1024 * 1024 * 1024

mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/mp4", ".m4v")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("audio/ogg", ".oga")
mimetypes.add_type("audio/ogg", ".opus")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/javascript", ".js")


# --- Aktivt quiz -------------------------------------------------------------------

class ActiveQuiz:
    def __init__(self, qid, data, media_dir):
        self.id = qid
        self.data = prepare(data)
        self.media_dir = media_dir
        self.rounds = self.data["rounds"]
        self.options = {}
        for r, rnd in enumerate(self.rounds):
            for q, item in enumerate(rnd["questions"]):
                opts = list(item["options"])
                random.Random(f"musikquiz-{qid}-{r}-{q}").shuffle(opts)
                self.options[(r, q)] = opts
        steps = [{"kind": "soundcheck"}, {"kind": "lobby"}, {"kind": "rules"}, {"kind": "rounds"}]
        for r, rnd in enumerate(self.rounds):
            steps.append({"kind": "round_intro", "r": r})
            steps += [{"kind": "question", "r": r, "q": q} for q in range(len(rnd["questions"]))]
            steps.append({"kind": "answers_intro", "r": r})
            steps += [{"kind": "answer", "r": r, "q": q} for q in range(len(rnd["questions"]))]
            steps.append({"kind": "scores", "r": r})
        steps += [{"kind": "final"}, {"kind": "thanks"}]
        self.steps = steps
        self.lock_step = {s["r"]: i for i, s in enumerate(steps) if s["kind"] == "answers_intro"}

    def correct(self, r, q):
        return self.rounds[r]["questions"][q]["options"][0]


def step_label(s):
    k = s["kind"]
    if "r" in s:
        rn = f"Runda {s['r'] + 1}"
        return {
            "round_intro": f"{rn} · start",
            "question": f"{rn} · fråga {s.get('q', 0) + 1}",
            "answers_intro": f"{rn} · facit",
            "answer": f"{rn} · svar {s.get('q', 0) + 1}",
            "scores": f"{rn} · ställning",
        }[k]
    return {"soundcheck": "Ljudtest", "lobby": "Välkommen / anslut", "rules": "Regler",
            "rounds": "Rundorna", "final": "Slutresultat", "thanks": "Tack"}[k]


# --- Spelläge ------------------------------------------------------------------------

class Game:
    def __init__(self, lib):
        self.lib = lib
        self.cond = threading.Condition()
        self.version = 1
        self.quiz = None
        self.session = secrets.token_hex(4)
        self.reset_state()

    def reset_state(self):
        self.step = 0
        self.teams = {}     # id -> {name, token, joined}
        self.answers = {}   # id -> {"r-q": text}
        self.marks = {}     # id -> {"r-q": bool}
        self.revealed = -1

    # Anropas med låset taget
    def activate(self, quiz, resume):
        self.quiz = quiz
        self.reset_state()
        if resume:
            try:
                with open(self.lib.state_file(quiz.id), encoding="utf-8") as f:
                    d = json.load(f)
                self.step = max(0, min(int(d.get("step", 0)), len(quiz.steps) - 1))
                self.teams = d.get("teams", {})
                self.answers = d.get("answers", {})
                self.marks = d.get("marks", {})
                self.revealed = int(d.get("revealed", -1))
            except (OSError, ValueError):
                pass
        self.session = secrets.token_hex(4)
        self.changed()

    def deactivate(self):
        self.quiz = None
        self.reset_state()
        self.changed()

    def save(self):
        if not self.quiz:
            return
        path = self.lib.state_file(self.quiz.id)
        try:
            library.write_json(path, {"step": self.step, "teams": self.teams, "answers": self.answers,
                                      "marks": self.marks, "revealed": self.revealed})
        except OSError as e:
            print(f"Varning: kunde inte spara läget: {e}", flush=True)

    def changed(self, persist=True):
        self.version += 1
        if persist:
            self.save()
        self.cond.notify_all()

    def poke(self):
        """Väcker väntande anrop utan att spara (t.ex. när tunneladressen blir klar)."""
        with self.cond:
            self.changed(persist=False)

    def wait(self, since):
        with self.cond:
            if since >= self.version:
                self.cond.wait(POLL_TIMEOUT)

    def current(self):
        return self.quiz.steps[self.step]

    def locked(self, r):
        return self.step >= self.quiz.lock_step[r]

    def open_questions(self):
        s = self.current()
        if s["kind"] == "question":
            return s["r"], s["q"]
        return None

    def revealed_upto(self, r):
        s = self.current()
        nq = len(self.quiz.rounds[r]["questions"])
        if s.get("r", -1) > r or s["kind"] in ("final", "thanks"):
            return nq - 1
        if s.get("r") == r:
            if s["kind"] == "answer":
                return s["q"]
            if s["kind"] == "scores":
                return nq - 1
        return -1

    def answer_hidden(self, r, q):
        s = self.current()
        return (s["kind"] == "answer" and s["r"] == r and s["q"] == q
                and self.quiz.rounds[r]["type"] == "video" and self.revealed != self.step)

    def is_correct(self, tid, r, q):
        key = f"{r}-{q}"
        m = self.marks.get(tid, {})
        if key in m:
            return m[key]
        return self.answers.get(tid, {}).get(key, "") == self.quiz.correct(r, q)

    def scores(self):
        rows = []
        for tid, t in self.teams.items():
            per_round = []
            for r in range(len(self.quiz.rounds)):
                hi = self.revealed_upto(r)
                per_round.append(sum(1 for q in range(hi + 1)
                                     if not self.answer_hidden(r, q) and self.is_correct(tid, r, q)))
            rows.append({"id": tid, "name": t["name"], "rounds": per_round, "total": sum(per_round)})
        rows.sort(key=lambda x: (-x["total"], x["name"].lower()))
        place = 0
        for i, row in enumerate(rows):
            if i == 0 or row["total"] != rows[i - 1]["total"]:
                place = i + 1
            row["place"] = place
        return rows

    def team_by_token(self, token):
        if not token:
            return None, None
        for tid, t in self.teams.items():
            if same(t["token"], token):
                return tid, t
        return None, None


def same(a, b):
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _json(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# --- Appen ---------------------------------------------------------------------------

class App:
    def __init__(self, lib=None):
        self.lib = lib or Library()
        self.game = Game(self.lib)
        self.admin_port = None
        self.public_port = None
        self.lan_port = None
        self.lan_server = None
        self.servers = []
        self.last_admin = time.monotonic()
        self.tunnel = None
        self.stopping = threading.Event()

    # Adresser
    def lan_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 80))   # skickar inget, väljer bara rätt nätverkskort
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return ""

    def lan_url(self):
        if not self.lan_server:
            return ""
        ip = self.lan_ip()
        return f"http://{ip}:{self.lan_port}/" if ip else ""

    def join_url(self):
        if self.tunnel and self.tunnel.status == "ready":
            return self.tunnel.url + "/"
        return self.lan_url()

    def connection(self):
        t = self.tunnel.info() if self.tunnel else {"status": "off", "url": "", "error": ""}
        return {"tunnel": t, "lan": bool(self.lan_server), "lanUrl": self.lan_url(), "joinUrl": self.join_url()}

    # Wifi-läge: publik lyssnare på alla nätverkskort
    def set_lan(self, enabled):
        if enabled and not self.lan_server:
            srv = None
            for port in (8780, 8781, 8782, 0):
                try:
                    srv = Server(("0.0.0.0", port), PublicHandler, self)
                    break
                except OSError:
                    continue
            if srv:
                self.lan_server, self.lan_port = srv, srv.server_address[1]
                threading.Thread(target=srv.serve_forever, daemon=True).start()
        elif not enabled and self.lan_server:
            srv, self.lan_server = self.lan_server, None
            threading.Thread(target=srv.shutdown, daemon=True).start()
        self.game.poke()

    # Start och stopp
    def start(self, admin_ports=range(8765, 8775)):
        admin = None
        for port in admin_ports:
            try:
                admin = Server(("127.0.0.1", port), AdminHandler, self)
                break
            except OSError:
                continue
        if not admin:
            raise SystemExit("Hittade ingen ledig port för Musikquiz.")
        public = Server(("127.0.0.1", 0), PublicHandler, self)
        self.admin_port, self.public_port = admin.server_address[1], public.server_address[1]
        self.servers = [admin, public]
        for s in self.servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        self.tunnel = Tunnel(self.public_port, self.lib.state_dir, on_change=self.game.poke)
        self.tunnel.kill_stale()
        if not os.environ.get("MUSIKQUIZ_NO_TUNNEL"):   # för lokala tester utan internetadress
            threading.Thread(target=self.tunnel.start, daemon=True).start()
        threading.Thread(target=self._idle_watch, daemon=True).start()
        return self.admin_port

    def _idle_watch(self):
        while not self.stopping.wait(30):
            if time.monotonic() - self.last_admin > IDLE_QUIT:
                print("Ingen värdsida öppen på länge – stänger Musikquiz.", flush=True)
                self.shutdown()

    def shutdown(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        if self.tunnel:
            self.tunnel.stop()
        for s in self.servers + ([self.lan_server] if self.lan_server else []):
            threading.Thread(target=s.shutdown, daemon=True).start()

    def wait(self):
        self.stopping.wait()
        time.sleep(0.5)

    # Vyer
    def round_info(self, r):
        rnd = self.game.quiz.rounds[r]
        return {"n": r + 1, "of": len(self.game.quiz.rounds), "title": rnd["title"], "type": rnd["type"],
                "instruction": rnd.get("instruction", ""), "count": len(rnd["questions"])}

    def question_label(self, r, q):
        t = self.game.quiz.rounds[r]["type"]
        return {"video": f"Låt {q + 1}", "image": f"Bild {q + 1}"}.get(t, f"Fråga {q + 1}")

    def player_view(self, tid):
        g = self.game
        if not g.quiz:
            return {"v": g.version, "kind": "idle", "session": g.session}
        quiz = g.quiz
        s = g.current()
        out = {"v": g.version, "kind": s["kind"], "title": quiz.data["title"], "session": g.session}
        if s["kind"] == "rules":
            out["rules"] = quiz.data["rules"]
        if s["kind"] == "rounds":
            out["rounds"] = [self.round_info(r) for r in range(len(quiz.rounds))]
        team = g.teams.get(tid) if tid else None
        if tid and not team:
            out["kicked"] = True
        if team:
            out["team"] = {"name": team["name"]}
        if "r" in s:
            r = s["r"]
            out["round"] = self.round_info(r)
            mine = g.answers.get(tid, {}) if team else {}
            open_q = g.open_questions()
            last = open_q[1] if open_q else (len(quiz.rounds[r]["questions"]) - 1 if g.locked(r) else -1)
            revealed = g.revealed_upto(r)
            qs = []
            for q in range(last + 1):
                item = {"q": q, "label": self.question_label(r, q), "answer": mine.get(f"{r}-{q}", ""),
                        "options": quiz.options[(r, q)]}
                if quiz.rounds[r]["type"] == "trivia":
                    item["text"] = quiz.rounds[r]["questions"][q].get("q", "")
                if q <= revealed and not g.answer_hidden(r, q):
                    item["facit"] = quiz.rounds[r]["questions"][q]["a"]
                    item["correctOption"] = quiz.correct(r, q)
                    item["correct"] = g.is_correct(tid, r, q) if team else False
                qs.append(item)
            out["questions"] = qs
            out["current"] = s.get("q")
            out["locked"] = g.locked(r)
        if s["kind"] in ("scores", "final", "thanks"):
            rows = g.scores()
            out["scores"] = [{"name": x["name"], "total": x["total"], "place": x["place"]} for x in rows[:10]]
            me = next((x for x in rows if x["id"] == tid), None)
            if me:
                out["me"] = {"total": me["total"], "place": me["place"], "of": len(rows)}
        return out

    def host_view(self):
        g = self.game
        out = {"v": g.version, "conn": self.connection()}
        if not g.quiz:
            out["kind"] = "idle"
            return out
        quiz = g.quiz
        s = g.current()
        out.update({"step": g.step, "steps": [step_label(x) for x in quiz.steps], "quizId": quiz.id,
                    "kind": s["kind"], "title": quiz.data["title"], "rules": quiz.data["rules"],
                    "soundcheck": quiz.data.get("soundcheck"), "outro": quiz.data.get("outro"),
                    "background": quiz.data.get("background"),
                    "rounds": [self.round_info(r) for r in range(len(quiz.rounds))]})
        teams = [{"id": tid, "name": t["name"]} for tid, t in g.teams.items()]
        teams.sort(key=lambda x: g.teams[x["id"]]["joined"])
        out["teams"] = teams
        if "r" in s:
            r = s["r"]
            out["round"] = self.round_info(r)
            if "q" in s:
                q = s["q"]
                item = dict(quiz.rounds[r]["questions"][q])
                item["options"] = quiz.options[(r, q)]
                item["correctOption"] = quiz.correct(r, q)
                item["label"] = self.question_label(r, q)
                out["question"] = item
                out["q"] = q
                key = f"{r}-{q}"
                if s["kind"] == "question":
                    out["answered"] = sum(1 for tid in g.teams if g.answers.get(tid, {}).get(key, "").strip())
                if s["kind"] == "answer":
                    out["revealed"] = not g.answer_hidden(r, q)
                    out["counts"] = {o: sum(1 for tid in g.teams if g.answers.get(tid, {}).get(key) == o)
                                     for o in quiz.options[(r, q)]}
                    out["teamAnswers"] = [{
                        "id": t["id"], "name": t["name"],
                        "answer": g.answers.get(t["id"], {}).get(key, ""),
                        "correct": g.is_correct(t["id"], r, q),
                        "manual": key in g.marks.get(t["id"], {}),
                    } for t in teams]
        if s["kind"] in ("scores", "final"):
            out["scores"] = g.scores()
        return out


# --- HTTP ----------------------------------------------------------------------------

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = os.name != "nt"

    def __init__(self, addr, handler, app):
        self.app = app
        super().__init__(addr, handler)


class BaseHandler(BaseHTTPRequestHandler):
    server_version = f"Musikquiz/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        p = getattr(self, "path", "")
        if not (p.startswith("/api/state") or p.startswith("/api/host/state") or p.startswith("/static/")
                or p.startswith("/api/admin/ping") or p.startswith("/media/") or p.startswith("/qmedia/")):
            super().log_message(fmt, *args)

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def err(self, code, msg):
        # Ett nekat anrop kan ha en oläst kropp kvar – stäng anslutningen i stället för att läsa den som nästa anrop.
        self.close_connection = True
        return self.send(code, _json({"error": msg}), headers={"Connection": "close"})

    def body(self, limit=8192):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            self.close_connection = True
            return None
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
            return d if isinstance(d, dict) else None
        except ValueError:
            return None

    def file(self, full, root, cache="no-cache", download_name=None):
        full = os.path.realpath(full)
        root = os.path.realpath(root)
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return self.send(404, b"Hittades inte", "text/plain; charset=utf-8")
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and size:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    return self.send(416, b"", headers={"Content-Range": f"bytes */{size}"})
                code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or not size:
            return
        with open(full, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def static(self, name):
        return self.file(os.path.join(STATIC_DIR, name), STATIC_DIR)

    # --- Lagens API (finns på båda portarna) ---------------------------------------
    def sent(self, *a, **k):
        """Som send(), men talar om att anropet är hanterat."""
        self.send(*a, **k)
        return True

    def erred(self, code, msg):
        self.err(code, msg)
        return True

    def player_get(self, p, qs):
        if p == "/api/health":
            return self.sent(200, _json({"ok": True, "app": "musikquiz"}))
        if p == "/api/state":
            since = int((qs.get("v") or ["0"])[0] or 0)
            token = (qs.get("token") or [""])[0]
            g = self.app.game
            g.wait(since)
            with g.cond:
                tid, _ = g.team_by_token(token) if g.quiz else (None, None)
                view = self.app.player_view(tid if tid else ("?" if token else None))
            return self.sent(200, _json(view))
        return None

    def player_post(self, p):
        if p not in ("/api/join", "/api/answer"):
            return None
        d = self.body()
        if d is None:
            return self.erred(400, "Ogiltig förfrågan.")
        g = self.app.game
        if p == "/api/join":
            name = re.sub(r"\s+", " ", str(d.get("name", ""))).strip()[:30]
            if len(name) < 2:
                return self.erred(400, "Skriv ett lagnamn (minst 2 tecken).")
            with g.cond:
                if not g.quiz:
                    return self.erred(409, "Inget quiz pågår just nu.")
                if any(t["name"].lower() == name.lower() for t in g.teams.values()):
                    return self.erred(409, "Det lagnamnet är redan taget. Välj ett annat.")
                if len(g.teams) >= MAX_TEAMS:
                    return self.erred(409, "Quizet är fullt.")
                tid = secrets.token_hex(4)
                token = secrets.token_urlsafe(16)
                g.teams[tid] = {"name": name, "token": token, "joined": time.time()}
                g.changed()
            return self.sent(200, _json({"token": token, "name": name}))
        try:
            r, q = int(d.get("r")), int(d.get("q"))
        except (TypeError, ValueError):
            return self.erred(400, "Ogiltig fråga.")
        text = re.sub(r"\s+", " ", str(d.get("text", ""))).strip()[:120]
        with g.cond:
            if not g.quiz:
                return self.erred(409, "Inget quiz pågår just nu.")
            tid, _ = g.team_by_token(str(d.get("token", "")))
            if not tid:
                return self.erred(401, "Laget finns inte längre. Anslut igen.")
            open_q = g.open_questions()
            if not open_q or open_q[0] != r or not (0 <= q <= open_q[1]):
                return self.erred(409, "Svaren är låsta.")
            if text and text not in g.quiz.options[(r, q)]:
                return self.erred(400, "Okänt alternativ.")
            g.answers.setdefault(tid, {})[f"{r}-{q}"] = text
            g.changed()
        return self.sent(200, b'{"ok":true}')


class PublicHandler(BaseHandler):
    """Det enda som nås utifrån: mobilsidan och lagens API."""

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            return self.static("play.html")
        if p.startswith("/static/") and p[8:] in PUBLIC_STATIC:
            return self.static(p[8:])
        if self.player_get(p, parse_qs(u.query)) is None:
            self.send(404, b"Hittades inte", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.player_post(urlparse(self.path).path) is None:
            self.err(404, "404")


PUBLIC_STATIC = {"style.css", "fonts/bebas-neue-latin-400-normal.woff2", "fonts/inter-latin-400-normal.woff2",
                 "fonts/inter-latin-600-normal.woff2", "fonts/inter-latin-700-normal.woff2", "fonts/fonts.css",
                 "icon.svg"}


class AdminHandler(BaseHandler):
    """Värdporten – bara 127.0.0.1. Skyddas mot andra webbplatser i webbläsaren med
    Host-kontroll (DNS-rebinding) och ett eget huvud på alla ändrande anrop (CSRF)."""

    def host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.app.admin_port}", f"localhost:{self.app.admin_port}")

    def csrf_ok(self):
        return self.headers.get("X-Musikquiz") == "1"

    def touch(self):
        self.app.last_admin = time.monotonic()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, b"Fel adress", "text/plain; charset=utf-8")
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        a = self.app
        lib = a.lib
        if p not in ("/api/state", "/api/health") and not p.startswith("/static/"):
            self.touch()
        pages = {"/": "library.html", "/index.html": "library.html", "/edit": "editor.html",
                 "/host": "host.html", "/play": "play.html", "/hjalp": "help.html"}
        if p in pages:
            return self.static(pages[p])
        if p.startswith("/static/"):
            return self.static(p[8:])
        if p.startswith("/media/"):
            q = a.game.quiz
            if not q:
                return self.send(404, b"", "text/plain")
            return self.file(os.path.join(q.media_dir, p[7:]), q.media_dir, cache="private, max-age=3600")
        if p.startswith("/qmedia/"):
            parts = p[8:].split("/", 1)
            try:
                d = lib.media_dir(parts[0])
            except KeyError:
                return self.send(404, b"", "text/plain")
            return self.file(os.path.join(d, parts[1] if len(parts) > 1 else ""), d, cache="no-cache")
        if p == "/api/admin/ping":
            return self.send(200, b'{"ok":true}')
        if p == "/api/admin/info":
            with a.game.cond:
                active = a.game.quiz.id if a.game.quiz else None
            return self.send(200, _json({"version": APP_VERSION, "quizzes": lib.summaries(), "active": active,
                                         "folder": lib.root, "conn": a.connection()}))
        if p == "/api/admin/quiz":
            qid = (qs.get("id") or [""])[0]
            try:
                quiz = lib.load(qid)
                return self.send(200, _json({"id": qid, "quiz": quiz, "media": lib.media_files(qid),
                                             "problems": check(quiz, lib.media_dir(qid))}))
            except (KeyError, OSError, ValueError):
                return self.err(404, "Quizet finns inte.")
        if p == "/api/admin/export":
            qid = (qs.get("id") or [""])[0]
            try:
                tmp, name = lib.export_zip(qid)
            except (KeyError, OSError) as e:
                return self.err(404, f"Kunde inte exportera: {e}")
            try:
                return self.file(tmp, lib.tmp_dir, download_name=name)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if p == "/api/host/state":
            since = int((qs.get("v") or ["0"])[0] or 0)
            a.game.wait(since)
            with a.game.cond:
                view = a.host_view()
            return self.send(200, _json(view))
        if self.player_get(p, qs) is None:
            self.send(404, b"Hittades inte", "text/plain; charset=utf-8")

    def do_PUT(self):
        if not self.host_ok() or not self.csrf_ok():
            return self.err(403, "Inte tillåtet.")
        self.touch()
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        lib = self.app.lib
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            return self.err(413, "Filen är för stor eller tom.")
        if p == "/api/admin/media":
            qid = (qs.get("id") or [""])[0]
            try:
                name = lib.media_name(qid, (qs.get("name") or [""])[0])
                dest = os.path.join(lib.media_dir(qid), name)
            except KeyError:
                return self.err(404, "Quizet finns inte.")
            except ValueError as e:
                self.drain(n)
                return self.err(400, str(e))
            if not self.receive(n, dest):
                return self.err(400, "Uppladdningen avbröts.")
            return self.send(200, _json({"name": name}))
        if p == "/api/admin/import":
            tmp = os.path.join(lib.tmp_dir, f"import-{secrets.token_hex(4)}.zip")
            if not self.receive(n, tmp):
                return self.err(400, "Uppladdningen avbröts.")
            try:
                qid = lib.import_zip(tmp)
            except ValueError as e:
                return self.err(400, str(e))
            except OSError as e:
                return self.err(500, f"Kunde inte importera: {e}")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return self.send(200, _json({"id": qid}))
        self.drain(n)
        return self.err(404, "404")

    def drain(self, n):
        while n > 0:
            chunk = self.rfile.read(min(1024 * 1024, n))
            if not chunk:
                break
            n -= len(chunk)

    def receive(self, n, dest):
        tmp = dest + ".part"
        try:
            with open(tmp, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        raise IOError("avbrutet")
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, dest)
            return True
        except (OSError, IOError):
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    def do_POST(self):
        if not self.host_ok():
            return self.err(403, "Fel adress.")
        p = urlparse(self.path).path
        if p in ("/api/join", "/api/answer"):
            return self.player_post(p)
        if not self.csrf_ok():
            return self.err(403, "Inte tillåtet.")
        self.touch()
        a = self.app
        lib = a.lib
        g = a.game
        if p == "/api/host/action":
            return self.host_action()
        d = self.body(MAX_QUIZ_JSON)
        if d is None:
            return self.err(400, "Ogiltig förfrågan.")
        try:
            if p == "/api/admin/ping":
                return self.send(200, b'{"ok":true}')
            if p == "/api/admin/quiz/new":
                quiz = d.get("quiz") if isinstance(d.get("quiz"), dict) else None
                qid = lib.create(str(d.get("title") or (quiz or {}).get("title") or "Nytt quiz"), quiz=quiz)
                return self.send(200, _json({"id": qid}))
            if p == "/api/admin/quiz/save":
                qid = str(d.get("id"))
                if not isinstance(d.get("quiz"), dict):
                    return self.err(400, "Quizet saknas.")
                lib.save(qid, d["quiz"])
                quiz = lib.load(qid)
                return self.send(200, _json({"ok": True, "problems": check(quiz, lib.media_dir(qid))}))
            if p == "/api/admin/quiz/duplicate":
                return self.send(200, _json({"id": lib.duplicate(str(d.get("id")))}))
            if p == "/api/admin/quiz/delete":
                qid = str(d.get("id"))
                with g.cond:
                    if g.quiz and g.quiz.id == qid:
                        g.deactivate()
                lib.delete(qid)
                return self.send(200, b'{"ok":true}')
            if p == "/api/admin/start":
                qid = str(d.get("id"))
                quiz = lib.load(qid)
                probs = check(quiz, lib.media_dir(qid))
                if probs:
                    return self.send(409, _json({"error": "Quizet är inte klart.", "problems": probs}))
                active = ActiveQuiz(qid, quiz, lib.media_dir(qid))
                with g.cond:
                    g.activate(active, bool(d.get("resume")))
                return self.send(200, b'{"ok":true}')
            if p == "/api/admin/stop":
                with g.cond:
                    g.deactivate()
                return self.send(200, b'{"ok":true}')
            if p == "/api/admin/lan":
                a.set_lan(bool(d.get("enabled")))
                return self.send(200, _json(a.connection()))
            if p == "/api/admin/tunnel/restart":
                threading.Thread(target=a.tunnel.start, daemon=True).start()
                return self.send(200, b'{"ok":true}')
            if p == "/api/admin/open-folder":
                target = lib.path(str(d["id"])) if d.get("id") else lib.root
                open_in_file_manager(target)
                return self.send(200, b'{"ok":true}')
            if p == "/api/admin/quit":
                self.send(200, b'{"ok":true}')
                threading.Timer(0.3, a.shutdown).start()
                return
        except KeyError:
            return self.err(404, "Quizet finns inte.")
        except (OSError, ValueError) as e:
            return self.err(500, f"Något gick fel: {e}")
        return self.err(404, "404")

    def host_action(self):
        d = self.body()
        if d is None:
            return self.err(400, "Ogiltig förfrågan.")
        g = self.app.game
        act = d.get("action")
        with g.cond:
            if not g.quiz:
                return self.send(200, _json(self.app.host_view()))
            last = len(g.quiz.steps) - 1
            if act == "next":
                g.step = min(g.step + 1, last)
            elif act == "prev":
                g.step = max(g.step - 1, 0)
            elif act == "reveal":
                g.revealed = g.step
            elif act == "goto":
                g.step = max(0, min(int(d.get("step", 0)), last))
            elif act == "mark":
                tid, key = str(d.get("team")), str(d.get("key"))
                if tid not in g.teams or not re.fullmatch(r"\d+-\d+", key):
                    return self.err(400, "mark")
                val = d.get("value")
                marks = g.marks.setdefault(tid, {})
                if val is None:
                    marks.pop(key, None)
                else:
                    marks[key] = bool(val)
            elif act == "kick":
                tid = str(d.get("team"))
                g.teams.pop(tid, None)
                g.answers.pop(tid, None)
                g.marks.pop(tid, None)
            elif act == "reset":
                g.reset_state()
            else:
                return self.err(400, "action")
            g.changed()
            view = self.app.host_view()
        return self.send(200, _json(view))


def open_in_file_manager(path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])
