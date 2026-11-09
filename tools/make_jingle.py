"""Skapar src/static/ljudtest.wav – en egenkomponerad slinga för ljudtestet (inga rättigheter behövs).

Ackorden C–Am–F–G som arpeggio med bas, 2 takter per ackord i 112 bpm. Körs en gång:
    python3 tools/make_jingle.py
"""
import math
import os
import struct
import wave

RATE = 22050
BPM = 112
BEAT = 60 / BPM
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "static", "ljudtest.wav")

CHORDS = [  # (bas, ackordtoner) som MIDI-nummer
    (48, [60, 64, 67, 72]),   # C
    (45, [57, 60, 64, 69]),   # Am
    (41, [53, 57, 60, 65]),   # F
    (43, [55, 59, 62, 67]),   # G
]


def freq(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def tone(buf, start, dur, f, amp, decay):
    i0 = int(start * RATE)
    n = int(dur * RATE)
    for i in range(n):
        if i0 + i >= len(buf):
            break
        t = i / RATE
        env = min(1.0, t / 0.01) * math.exp(-t * decay)
        s = math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t) + 0.1 * math.sin(6 * math.pi * f * t)
        buf[i0 + i] += amp * env * s


def main():
    bars_per_chord = 2
    total = len(CHORDS) * bars_per_chord * 4 * BEAT
    buf = [0.0] * int(total * RATE)
    t = 0.0
    pattern = [0, 1, 2, 3, 2, 1, 2, 3]
    for bass, notes in CHORDS:
        for bar in range(bars_per_chord):
            tone(buf, t, 4 * BEAT, freq(bass), 0.32, 1.2)
            for k, idx in enumerate(pattern):
                tone(buf, t + k * BEAT / 2, BEAT * 1.5, freq(notes[idx]), 0.16, 3.0)
            t += 4 * BEAT
    peak = max(abs(x) for x in buf) or 1
    with wave.open(OUT, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(x / peak * 0.85 * 32767)) for x in buf))
    print(f"Skrev {os.path.abspath(OUT)} ({total:.1f} s)")


if __name__ == "__main__":
    main()
