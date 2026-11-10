"""Skapar appens egna ljud – inga rättigheter behövs:

  src/static/demo/*.wav    melodier till exempelquizets låtrunda (traditionella visor, fria att använda)
  src/static/sounds/*.wav  korta ljudeffekter till storskärmen (ny runda, trumvirvel, vinnare)

Körs en gång:  python3 tools/make_sounds.py
"""
import math
import os
import random
import struct
import wave

RATE = 22050
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "static")


def freq(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def buf_for(seconds):
    return [0.0] * int(seconds * RATE)


def add_tone(buf, start, dur, f, amp, decay=3.0, harm=(1, .35, .15, .05), attack=.008):
    i0 = int(start * RATE)
    for i in range(int(dur * RATE)):
        if i0 + i >= len(buf):
            break
        t = i / RATE
        env = min(1.0, t / attack) * math.exp(-t * decay)
        # mjuk avtoning i slutet av tonen så att det inte klickar
        tail = min(1.0, (dur - t) / 0.03)
        s = sum(h * math.sin(2 * math.pi * f * (k + 1) * t) for k, h in enumerate(harm))
        buf[i0 + i] += amp * env * tail * s


def add_noise(buf, start, dur, amp, decay, rnd, lowpass=0.0):
    i0 = int(start * RATE)
    prev = 0.0
    for i in range(int(dur * RATE)):
        if i0 + i >= len(buf):
            break
        t = i / RATE
        x = rnd.uniform(-1, 1)
        prev = prev * lowpass + x * (1 - lowpass)
        buf[i0 + i] += amp * math.exp(-t * decay) * prev


def save(buf, path, peak_level=0.85):
    peak = max(abs(x) for x in buf) or 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(x / peak * peak_level * 32767)) for x in buf))
    print(f"  {os.path.relpath(path, ROOT)}  ({len(buf) / RATE:.1f} s)")


# --- Melodier ------------------------------------------------------------------------------
C4, D4, E4, F4, G4, A4, B4 = 60, 62, 64, 65, 67, 69, 71
C5, D5, E5, F5, G5, A5, B5, C6 = 72, 74, 76, 77, 79, 81, 83, 84

SONGS = {
    # Twinkle, twinkle little star / Blinka lilla stjärna (fransk folkmelodi, 1700-talet)
    "blinka-lilla-stjarna": (100, [
        (C5, 1), (C5, 1), (G5, 1), (G5, 1), (A5, 1), (A5, 1), (G5, 2),
        (F5, 1), (F5, 1), (E5, 1), (E5, 1), (D5, 1), (D5, 1), (C5, 2),
        (G5, 1), (G5, 1), (F5, 1), (F5, 1), (E5, 1), (E5, 1), (D5, 2)]),
    # Frère Jacques / Broder Jakob (fransk kanon, 1700-talet)
    "broder-jakob": (116, [
        (C5, 1), (D5, 1), (E5, 1), (C5, 1), (C5, 1), (D5, 1), (E5, 1), (C5, 1),
        (E5, 1), (F5, 1), (G5, 2), (E5, 1), (F5, 1), (G5, 2),
        (G5, .5), (A5, .5), (G5, .5), (F5, .5), (E5, 1), (C5, 1),
        (G5, .5), (A5, .5), (G5, .5), (F5, .5), (E5, 1), (C5, 1),
        (C5, 1), (G4, 1), (C5, 2)]),
    # Itsy bitsy spider / Imse vimse spindel (traditionell)
    "imse-vimse-spindel": (104, [
        (G4, .5), (C5, 1), (C5, .5), (C5, 1), (D5, .5), (E5, 1.5),
        (E5, 1), (D5, .5), (C5, 1), (D5, .5), (E5, 1), (C5, 1.5),
        (E5, 1.5), (E5, 1), (F5, .5), (G5, 1.5), (G5, 1.5),
        (F5, 1), (E5, .5), (F5, 1), (G5, .5), (E5, 2)]),
    # Happy Birthday (melodin från 1893 är allmän egendom)
    "happy-birthday": (96, [
        (G4, .75), (G4, .25), (A4, 1), (G4, 1), (C5, 1), (B4, 2),
        (G4, .75), (G4, .25), (A4, 1), (G4, 1), (D5, 1), (C5, 2),
        (G4, .75), (G4, .25), (G5, 1), (E5, 1), (C5, 1), (B4, 1), (A4, 2),
        (F5, .75), (F5, .25), (E5, 1), (C5, 1), (D5, 1), (C5, 2)]),
}


def make_song(name, bpm, notes):
    beat = 60 / bpm
    total = sum(d for _, d in notes) * beat + 1.2
    buf = buf_for(total)
    t = 0.3
    for n, d in notes:
        add_tone(buf, t, d * beat + .35, freq(n), .5, decay=2.2, harm=(1, .45, .2, .08, .04))
        add_tone(buf, t, d * beat + .2, freq(n - 12), .12, decay=3.0, harm=(1, .2))   # lite djup
        t += d * beat
    save(buf, os.path.join(ROOT, "demo", name + ".wav"))


# --- Ljudeffekter ----------------------------------------------------------------------------
BRASS = (1, .7, .5, .35, .2, .12, .06)


def make_round():
    """Kort fanfar till en ny runda."""
    buf = buf_for(2.0)
    for i, (n, t) in enumerate([(G4, 0), (C5, .12), (E5, .24), (G5, .36)]):
        add_tone(buf, t, .5 if i < 3 else 1.5, freq(n), .4, decay=2.5 if i < 3 else 1.4, harm=BRASS, attack=.02)
    for n in (C5, E5, G5):
        add_tone(buf, .36, 1.5, freq(n), .15, decay=1.6, harm=BRASS, attack=.03)
    save(buf, os.path.join(ROOT, "sounds", "runda.wav"))


def make_drumroll():
    """Trumvirvel som växer, avslutas med ett cymbalslag."""
    rnd = random.Random(7)
    buf = buf_for(3.4)
    t, n = 0.0, 0
    while t < 2.3:
        level = .15 + .85 * (t / 2.3) ** 1.5
        add_noise(buf, t, .06, .35 * level, 45, rnd, lowpass=.35)
        add_tone(buf, t, .05, 190, .12 * level, decay=60, harm=(1,))
        t += .045 - .012 * (t / 2.3)
        n += 1
    add_noise(buf, 2.35, 1.0, .9, 3.2, rnd, lowpass=.05)       # cymbal
    add_tone(buf, 2.35, .4, 70, .9, decay=8, harm=(1, .3))      # baskagge
    save(buf, os.path.join(ROOT, "sounds", "trumvirvel.wav"))


def make_winner():
    """Segerfanfar när vinnaren avslöjas."""
    buf = buf_for(3.6)
    seq = [(C5, 0, .22), (C5, .25, .22), (C5, .5, .22), (C5, .75, .6), (G4 + 1, 1.35, .3), (A4 + 1, 1.65, .3), (C5, 1.95, 1.6)]
    for n, t, d in seq:
        add_tone(buf, t, d + .2, freq(n), .4, decay=1.8, harm=BRASS, attack=.02)
    for n in (C4, E4, G4, C5, E5):
        add_tone(buf, 1.95, 1.6, freq(n), .12, decay=1.2, harm=BRASS, attack=.04)
    save(buf, os.path.join(ROOT, "sounds", "vinnare.wav"))


def make_reveal():
    """Kort 'ding' när en plats på prispallen avslöjas."""
    buf = buf_for(1.2)
    add_tone(buf, 0, 1.1, freq(E5 + 12), .35, decay=3.5, harm=(1, .2, .1))
    add_tone(buf, .08, 1.0, freq(B5 + 12), .25, decay=3.5, harm=(1, .2))
    save(buf, os.path.join(ROOT, "sounds", "ding.wav"))


if __name__ == "__main__":
    print("Skapar ljud i", os.path.abspath(ROOT))
    for name, (bpm, notes) in SONGS.items():
        make_song(name, bpm, notes)
    make_round()
    make_drumroll()
    make_winner()
    make_reveal()
