"""
verify_stream.py — end-to-end verification of the OpenUI stream endpoint.
Connects to localhost:8090, reads the full SSE stream, reassembles all tokens
into OpenUI Lang source, parses all RiskCard blocks, and validates each card.
"""
import socket
import json
import re

EXPECTED = [
    ("MV Pacific Star",    "CRITICAL", "DIVERTED",   True),
    ("MV Coral Queen",     "HIGH",     "DELAYED",    True),
    ("MV Asian Horizon",   "MEDIUM",   "DELAYED",    False),
    ("MV Northern Light",  "LOW",      "IN_TRANSIT", False),
    ("MV Atlantic Bridge", "LOW",      "IN_TRANSIT", False),
]


def read_full_stream(host="127.0.0.1", port=8090, timeout=45):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.sendall(
        b"POST /api/stream-risk HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 0\r\n"
        b"Connection: close\r\n\r\n"
    )
    s.settimeout(timeout)
    buf = b""
    try:
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            buf += chunk
    except OSError:
        pass
    finally:
        s.close()
    return buf.decode("utf-8", errors="replace")


def reassemble_tokens(raw_http):
    bi = raw_http.find("\r\n\r\n")
    body = raw_http[bi + 4:] if bi != -1 else raw_http

    source = ""
    done_seen = False
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
            if ev.get("type") == "token":
                source += ev["content"]
            elif ev.get("type") == "done":
                done_seen = True
        except (json.JSONDecodeError, KeyError):
            pass
    return source, done_seen


def parse_cards(source):
    card_re = re.compile(r"<RiskCard([\s\S]*?)>([\s\S]*?)</RiskCard>", re.MULTILINE)
    attr_re = re.compile(r'(\w+)="([^"]*)"')

    results = []
    for m in card_re.finditer(source):
        attrs = dict(attr_re.findall(m.group(1)))
        content = m.group(2).strip()
        slots = [s.strip() for s in content.split("||")]
        results.append({
            "attrs":       attrs,
            "reasoning":   slots[0] if len(slots) > 0 else "",
            "alt_route":   slots[1] if len(slots) > 1 else "",
        })
    return results


def main():
    print("Connecting to localhost:8090 …")
    raw = read_full_stream()
    source, done_seen = reassemble_tokens(raw)

    print(f"Total chars assembled : {len(source)}")
    print(f"done event received   : {done_seen}")
    print()

    cards = parse_cards(source)
    print(f"RiskCard blocks found : {len(cards)}")
    print()
    print("-" * 60)

    all_ok = True

    for i, card in enumerate(cards):
        attrs     = card["attrs"]
        reasoning = card["reasoning"]
        alt_route = card["alt_route"]

        vessel   = attrs.get("vessel",   "?")
        severity = attrs.get("severity", "?")
        status   = attrs.get("status",   "?")
        wind     = attrs.get("wind",     "?")
        storm    = attrs.get("storm",    "?")
        wave     = attrs.get("wave",     "?")
        eta      = attrs.get("eta_hours","0")

        issues = []

        if i < len(EXPECTED):
            ev, es, est, needs_alt = EXPECTED[i]
            if vessel   != ev:  issues.append(f"vessel expected '{ev}' got '{vessel}'")
            if severity != es:  issues.append(f"severity expected '{es}' got '{severity}'")
            if status   != est: issues.append(f"status expected '{est}' got '{status}'")
            if needs_alt and alt_route.strip() in ("", "null"):
                issues.append(f"alt_route required for {severity} but got '{alt_route.strip()}'")
        else:
            issues.append("extra card (unexpected)")

        if not reasoning:
            issues.append("reasoning is empty")

        ok = not issues
        all_ok = all_ok and ok
        tag = "OK" if ok else "FAIL"

        print(f"Card {i+1}: {vessel}  [{tag}]")
        print(f"  severity={severity}  status={status}  wind={wind}kn  storm={storm}  wave={wave}m  eta={eta}h")
        print(f"  reasoning : {reasoning[:80]}")
        print(f"  alt_route : {alt_route.strip()[:60] or '(none)'}")
        if issues:
            for iss in issues:
                print(f"  !! {iss}")
        print()

    print("-" * 60)
    if all_ok and len(cards) == 5:
        print("STREAM VERIFIED — all 5 RiskCard blocks valid")
    else:
        print(f"ISSUES: {len(cards)} cards found, all_ok={all_ok}")

    return 0 if (all_ok and len(cards) == 5) else 1


if __name__ == "__main__":
    raise SystemExit(main())
