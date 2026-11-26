#!/usr/bin/env python3
"""Capture SSE interarrival times to decode Orbital Privacy Screwer's Morse.

Portable adaptation of the historical morse4.py (not a new remote run).
Usage: python3 capture_morse.py https://INSTANCE.example --runs 6
Only Python standard library is used.
"""
import argparse
import http.client
import json
import statistics
import time
import urllib.parse

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----."
}
INV = {v: k for k, v in MORSE.items()}

def capture(url, index):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Pass a complete HTTP(S) URL")
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = cls(parsed.hostname, parsed.port, timeout=60)
    query = urllib.parse.urlencode({"q": f"v{index}", "reqId": f"Q{index}", "sessionId": f"S{index}"})
    endpoint = (parsed.path.rstrip("/") or "") + "/api/acquire?" + query
    connection.request("GET", endpoint)
    response = connection.getresponse()
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}")
    arrivals = []
    while True:
        line = response.fp.readline()
        if not line:
            break
        now = time.perf_counter()
        data = line.strip()
        if not data.startswith(b"data: "):
            continue
        try:
            event = json.loads(data[6:])
        except json.JSONDecodeError:
            continue
        if event.get("type") in ("step", "complete"):
            arrivals.append(now)
        if event.get("type") == "complete":
            break
    connection.close()
    return [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("url", help="URL of your own running challenge instance")
    p.add_argument("--runs", type=int, default=6)
    args = p.parse_args()
    runs = []
    for index in range(args.runs):
        deltas = capture(args.url, index)
        if len(deltas) == 67 and min(deltas) > 25 and max(deltas) < 320:
            runs.append(deltas)
            print(f"run {index}: accepted 67 intervals")
        else:
            print(f"run {index}: rejected ({len(deltas)} intervals)")
    if not runs:
        raise RuntimeError("No complete, low-jitter runs")
    median = [statistics.median(row[i] for row in runs) for i in range(67)]
    morse = "".join("." if x < 100 else ("-" if x < 170 else " ") for x in median)
    words = morse.split(" ")
    decoded = "".join(INV.get(word, "?") for word in words)
    print("good runs:", len(runs))
    print("morse:", morse)
    print("raw transmission:", decoded)
    print("counts:", morse.count("."), morse.count("-"), morse.count(" "))
    print("median intervals (ms):", [round(x, 1) for x in median])

if __name__ == "__main__":
    main()
