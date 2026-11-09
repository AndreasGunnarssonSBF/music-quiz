"""Quizbiblioteket: quizmappar på disk, kontroll, import och export.

Varje quiz är en mapp med quiz.json och en undermapp media/. Samma format som
quiz-app/, så en zippad kopia av den mappen går att importera direkt.
"""
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import zipfile

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT
ROUND_TYPES = ("trivia", "image", "video")
MAX_OPTIONS = 4


def home_dir():
    base = os.environ.get("MUSIKQUIZ_HOME") or os.path.join(os.path.expanduser("~"), "Musikquiz")
    os.makedirs(base, exist_ok=True)
    return base


class Library:
    def __init__(self, root=None):
        self.root = root or home_dir()
        self.quiz_dir = os.path.join(self.root, "Quiz")
        self.trash_dir = os.path.join(self.root, "Papperskorg")
        self.state_dir = os.path.join(self.root, ".state")
        self.tmp_dir = os.path.join(self.root, ".tmp")
        for d in (self.quiz_dir, self.state_dir, self.tmp_dir):
            os.makedirs(d, exist_ok=True)
        # Gamla halvfärdiga uppladdningar
        for f in os.listdir(self.tmp_dir):
            try:
                os.remove(os.path.join(self.tmp_dir, f))
            except OSError:
                pass
        if not self.list_ids():
            self.create_example()

    # --- Sökvägar -------------------------------------------------------------
    def path(self, qid):
        if not valid_id(qid):
            raise KeyError(qid)
        p = os.path.join(self.quiz_dir, qid)
        if not os.path.isfile(os.path.join(p, "quiz.json")):
            raise KeyError(qid)
        return p

    def media_dir(self, qid):
        d = os.path.join(self.path(qid), "media")
        os.makedirs(d, exist_ok=True)
        return d

    def list_ids(self):
        out = []
        for name in os.listdir(self.quiz_dir):
            if valid_id(name) and os.path.isfile(os.path.join(self.quiz_dir, name, "quiz.json")):
                out.append(name)
        return out

    # --- Läsa och skriva --------------------------------------------------------
    def load(self, qid):
        with open(os.path.join(self.path(qid), "quiz.json"), encoding="utf-8") as f:
            return json.load(f)

    def save(self, qid, quiz):
        p = os.path.join(self.path(qid), "quiz.json")
        write_json(p, clean_quiz(quiz))

    def media_files(self, qid):
        d = self.media_dir(qid)
        return sorted(f for f in os.listdir(d) if os.path.splitext(f)[1].lower() in MEDIA_EXT)

    def summary(self, qid):
        quiz = self.load(qid)
        d = self.media_dir(qid)
        size = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        rounds = quiz.get("rounds") or []
        return {
            "id": qid,
            "title": quiz.get("title") or qid,
            "rounds": len(rounds),
            "questions": sum(len(r.get("questions") or []) for r in rounds),
            "bytes": size,
            "problems": check(quiz, d),
            "modified": os.path.getmtime(os.path.join(self.path(qid), "quiz.json")),
            "hasSaved": self.has_saved_game(qid),
            "aiUnchecked": sum(1 for r in rounds for q in (r.get("questions") or []) if q.get("ai")),
        }

    def summaries(self):
        rows = []
        for qid in self.list_ids():
            try:
                rows.append(self.summary(qid))
            except (OSError, ValueError) as e:
                rows.append({"id": qid, "title": qid, "rounds": 0, "questions": 0, "bytes": 0,
                             "problems": [f"quiz.json går inte att läsa: {e}"], "modified": 0, "hasSaved": False})
        rows.sort(key=lambda r: -r["modified"])
        return rows

    # --- Skapa, kopiera, ta bort -------------------------------------------------
    def new_id(self, title):
        base = slug(title) or "quiz"
        qid, n = base, 2
        while os.path.exists(os.path.join(self.quiz_dir, qid)):
            qid = f"{base}-{n}"
            n += 1
        return qid

    def create(self, title, quiz=None):
        title = (title or "").strip() or "Nytt quiz"
        qid = self.new_id(title)
        p = os.path.join(self.quiz_dir, qid)
        os.makedirs(os.path.join(p, "media"))
        quiz = quiz or new_quiz(title)
        quiz["title"] = title
        if "rules" not in quiz:   # t.ex. quiz från AI: använd standardreglerna
            quiz["rules"] = new_quiz(title)["rules"]
        write_json(os.path.join(p, "quiz.json"), clean_quiz(quiz))
        return qid

    def duplicate(self, qid):
        quiz = self.load(qid)
        title = f"{quiz.get('title') or qid} (kopia)"
        new = self.new_id(title)
        shutil.copytree(self.path(qid), os.path.join(self.quiz_dir, new))
        quiz["title"] = title
        self.save(new, quiz)
        return new

    def delete(self, qid):
        """Flyttar quizet till Papperskorg/ i stället för att radera det."""
        src = self.path(qid)
        os.makedirs(self.trash_dir, exist_ok=True)
        dst = os.path.join(self.trash_dir, f"{qid}-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(src, dst)
        try:
            os.remove(self.state_file(qid))
        except OSError:
            pass

    # --- Media ------------------------------------------------------------------
    def media_name(self, qid, filename):
        """Säkert, unikt filnamn i quizets mediemapp."""
        stem, ext = os.path.splitext(os.path.basename(filename or ""))
        ext = ext.lower()
        if ext not in MEDIA_EXT:
            raise ValueError("Filtypen stöds inte. Använd bild (jpg, png, gif, webp), video (mp4, webm, mov) eller ljud (mp3, m4a, wav).")
        stem = slug(stem)[:60] or "fil"
        d = self.media_dir(qid)
        name, n = stem + ext, 2
        while os.path.exists(os.path.join(d, name)):
            name = f"{stem}-{n}{ext}"
            n += 1
        return name

    # --- Sparat spelläge ------------------------------------------------------------
    def state_file(self, qid):
        return os.path.join(self.state_dir, f"{qid}.json")

    def has_saved_game(self, qid):
        try:
            with open(self.state_file(qid), encoding="utf-8") as f:
                d = json.load(f)
            return bool(d.get("teams")) or int(d.get("step", 0)) > 1
        except (OSError, ValueError):
            return False

    # --- Export / import ------------------------------------------------------------
    def export_zip(self, qid):
        """Skriver quizet till en tillfällig zip-fil och returnerar (sökväg, filnamn)."""
        quiz = self.load(qid)
        d = self.media_dir(qid)
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=self.tmp_dir)
        os.close(fd)
        used = used_media(quiz)
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr("quiz.json", json.dumps(quiz, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
            for f in sorted(used):
                full = os.path.join(d, f)
                if os.path.isfile(full):
                    # Media är redan komprimerad – lagras utan komprimering, går mycket snabbare.
                    z.write(full, "media/" + f, compress_type=zipfile.ZIP_STORED)
        name = (quiz.get("title") or qid).strip()
        name = re.sub(r'[\\/:*?"<>|]+', "", name)[:80] or qid
        return tmp, f"{name}.musikquiz.zip"

    def import_zip(self, zip_path):
        """Packar upp en quizfil (zip med quiz.json och media/) som ett nytt quiz. Returnerar id."""
        try:
            z = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            raise ValueError("Filen är ingen giltig quizfil (zip).")
        with z:
            names = [n for n in z.namelist() if not n.startswith("__MACOSX/") and not n.endswith("/")]
            qjson = [n for n in names if os.path.basename(n) == "quiz.json"]
            if not qjson:
                raise ValueError("Quizfilen saknar quiz.json.")
            qjson.sort(key=lambda n: n.count("/"))
            top = qjson[0][: -len("quiz.json")]
            try:
                quiz = json.loads(z.read(qjson[0]).decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("quiz.json i quizfilen går inte att läsa.")
            if not isinstance(quiz, dict) or not isinstance(quiz.get("rounds", []), list):
                raise ValueError("quiz.json har fel format.")
            qid = self.create(quiz.get("title") or "Importerat quiz", quiz={"title": "", "rounds": []})
            mdir = self.media_dir(qid)
            for n in names:
                if not n.startswith(top) or n == qjson[0]:
                    continue
                base = os.path.basename(n)
                if base.startswith(".") or os.path.splitext(base)[1].lower() not in MEDIA_EXT:
                    continue
                with z.open(n) as src, open(os.path.join(mdir, base), "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        upgrade_legacy(quiz, mdir)
        self.save(qid, quiz)
        return qid

    # --- Exempel ----------------------------------------------------------------
    def create_example(self):
        self.create("Exempelquiz", quiz=example_quiz())


# --- Hjälpfunktioner -------------------------------------------------------------

def valid_id(qid):
    return bool(qid) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", qid) is not None


def slug(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:50].strip("-")


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def new_quiz(title):
    return {
        "title": title,
        "rules": [
            "Inget fusk – mobilen används bara för att svara i quizet, inte för att googla eller använda Shazam.",
            "Varje fråga har flera alternativ. Tryck på det ni tror är rätt – ni kan ändra er tills rundan låses.",
            "Ett lag = en mobil. Ange ert lagnamn när ni ansluter.",
            "Quizmasterns beslut är slutgiltigt.",
        ],
        "rounds": [],
    }


def example_quiz():
    q = new_quiz("Exempelquiz")
    q["rounds"] = [{
        "title": "Musiktrivia",
        "type": "trivia",
        "instruction": "Svara på frågan",
        "questions": [
            {"q": "Vilket år vann ABBA Eurovision Song Contest med ”Waterloo”?",
             "options": ["1974", "1972", "1976", "1978"],
             "fact": "Finalen hölls i Brighton i England. Det var Sveriges första seger i tävlingen."},
            {"q": "Hur många tangenter har ett vanligt piano?",
             "options": ["88", "76", "92", "64"],
             "fact": "52 vita och 36 svarta tangenter."},
            {"q": "Vilken svensk grupp gick upp till förstaplatsen på amerikanska Billboardlistan med ”The Look” 1989?",
             "options": ["Roxette", "Europe", "Ace of Base", "The Cardigans"]},
            {"q": "Vilket var det sista albumet som The Beatles spelade in tillsammans?",
             "options": ["Abbey Road", "Let It Be", "Revolver", "Help!"],
             "fact": "”Let It Be” gavs ut sist (1970), men ”Abbey Road” (1969) var det sista de spelade in."},
        ],
    }]
    return q


def used_media(quiz):
    """Alla mediafiler som quizet hänvisar till."""
    out = set()
    for key in ("soundcheck", "outro", "background"):
        if quiz.get(key):
            out.add(quiz[key])
    for r in quiz.get("rounds") or []:
        for q in r.get("questions") or []:
            for key in ("image", "questionImage", "video", "answerImage"):
                if q.get(key):
                    out.add(q[key])
            out.update(x for x in (q.get("images") or []) if x)
    return {os.path.basename(str(x)) for x in out}


def upgrade_legacy(quiz, media_dir):
    """Quiz från den första versionen hade ljudtest, outro och bakgrund med fasta filnamn."""
    for key, name in (("soundcheck", "ljudtest.m4a"), ("outro", "outro.m4a"), ("background", "noter.gif")):
        if not quiz.get(key) and os.path.isfile(os.path.join(media_dir, name)):
            quiz[key] = name


def _num(x):
    try:
        v = float(x)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def clean_quiz(quiz):
    """Rensar bort okända fält och tvättar värden innan quiz.json skrivs."""
    s = lambda v, n=500: str(v or "").strip()[:n]
    f = lambda v: os.path.basename(str(v)) if v else ""
    out = {"title": s(quiz.get("title"), 120) or "Quiz",
           "rules": [s(x, 300) for x in (quiz.get("rules") or []) if s(x)]}
    for key in ("soundcheck", "outro", "background"):
        if quiz.get(key):
            out[key] = f(quiz[key])
    rounds = []
    for r in quiz.get("rounds") or []:
        typ = r.get("type") if r.get("type") in ROUND_TYPES else "trivia"
        rr = {"title": s(r.get("title"), 80), "type": typ, "instruction": s(r.get("instruction"), 120), "questions": []}
        for q in r.get("questions") or []:
            qq = {"options": [s(o, 120) for o in (q.get("options") or [])][:MAX_OPTIONS]}
            for key, n in (("q", 600), ("a", 200), ("fact", 1200), ("song", 160)):
                if s(q.get(key)):
                    qq[key] = s(q.get(key), n)
            for key in ("image", "questionImage", "video", "answerImage"):
                if q.get(key):
                    qq[key] = f(q[key])
            if q.get("ai"):
                qq["ai"] = True   # skapad av AI, inte kontrollerad ännu
            imgs = [f(x) for x in (q.get("images") or []) if x]
            if imgs:
                qq["images"] = imgs
            trim = q.get("trim")
            if isinstance(trim, list) and len(trim) == 2:
                a, b = _num(trim[0]), _num(trim[1])
                if a is not None or b is not None:
                    qq["trim"] = [a or 0, b]
            rr["questions"].append(qq)
        rounds.append(rr)
    out["rounds"] = rounds
    return out


def check(quiz, media_dir):
    """Problem som hindrar att quizet startas, på svenska."""
    probs = []
    have = set(os.listdir(media_dir)) if os.path.isdir(media_dir) else set()

    def missing(where, f, what):
        if f and os.path.basename(f) not in have:
            probs.append(f"{where}: {what} ”{f}” saknas i mediemappen.")

    rounds = quiz.get("rounds") or []
    if not rounds:
        probs.append("Quizet har inga rundor ännu.")
    for key, what in (("soundcheck", "ljudtestfilen"), ("outro", "avslutningsmusiken"), ("background", "bakgrundsbilden")):
        missing("Inställningar", quiz.get(key), what)
    for ri, r in enumerate(rounds):
        rname = f"Runda {ri + 1}"
        qs = r.get("questions") or []
        if not (r.get("title") or "").strip():
            probs.append(f"{rname}: rundan saknar namn.")
        if not qs:
            probs.append(f"{rname}: rundan har inga frågor.")
        typ = r.get("type")
        for qi, q in enumerate(qs):
            where = f"{rname}, fråga {qi + 1}"
            opts = [str(o).strip() for o in (q.get("options") or [])]
            filled = [o for o in opts if o]
            if not opts or not opts[0]:
                probs.append(f"{where}: rätt svar saknas.")
            if len(filled) < 2:
                probs.append(f"{where}: minst ett felaktigt alternativ behövs.")
            if len(set(o.lower() for o in filled)) != len(filled):
                probs.append(f"{where}: två alternativ är likadana.")
            if typ == "trivia" and not (q.get("q") or "").strip():
                probs.append(f"{where}: frågetexten saknas.")
            if typ == "image" and not (q.get("image") or q.get("questionImage")):
                probs.append(f"{where}: bilden saknas.")
            if typ == "video" and not q.get("video"):
                probs.append(f"{where}: låt- eller videofilen saknas.")
            for key, what in (("image", "bilden"), ("questionImage", "frågebilden"), ("video", "filen"), ("answerImage", "facitbilden")):
                missing(where, q.get(key), what)
            for x in q.get("images") or []:
                missing(where, x, "bilden")
    return probs


def prepare(quiz):
    """Gör ett startbart quiz av quiz.json: tomma alternativ bort, facit och bilder får standardvärden."""
    out = json.loads(json.dumps(quiz))
    for r in out["rounds"]:
        for q in r["questions"]:
            q["options"] = [str(o).strip() for o in q.get("options") or [] if str(o).strip()]
            q["a"] = (q.get("a") or "").strip() or q["options"][0]
            if r["type"] == "image":
                q["image"] = q.get("image") or q.get("questionImage")
            trim = q.get("trim")
            if trim and not (trim[1] is None or trim[1] > trim[0]):
                q["trim"] = [trim[0], None]
    out.setdefault("rules", [])
    return out
