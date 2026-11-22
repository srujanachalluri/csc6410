#!/usr/bin/env python3
"""
analyze_wg.py - CSC 6410 Experiment 7 analysis script
Author: Sareena Challuri

PURPOSE
  Read my three captures and print, for each one: packet count, packet sizes and
  protocols. For the tunnel capture it also prints the WireGuard message type of
  every UDP payload, and for matching packets the outer-minus-inner size (what the
  tunnel adds to each packet).

LOGIC
  1. tshark (Wireshark's command-line form) turns each pcap into one line per
     packet with the fields I need (-T fields).
  2. Capture 2 is decoded as WireGuard with -d udp.port==51820,wg.
  3. Captures 2 and 3 were taken at the same time, so the n-th "transport data"
     packet on the underlay carries the n-th packet seen on wg0. I pair them in
     order and subtract the IP lengths: outer IP + UDP + WireGuard header + tag +
     padding.

ASSUMPTIONS
  - Files are named 1_underlay_plain.pcap, 2_underlay_tunnel.pcap, 3_inner_wg0.pcap.
  - No keepalives (I did not set persistent-keepalive), so every transport-data
    packet carries one inner packet.

TESTING
  - The script prints the counts it pairs; if the two lists differ in length it
    says so instead of guessing.

LIMITS / FALLBACK
  - Needs tshark. If a capture is empty it prints "empty" and goes on.
"""
import subprocess
import sys
from collections import Counter
from pathlib import Path

WG_TYPES = {"1": "handshake initiation", "2": "handshake response", "3": "cookie reply", "4": "transport data"}
FIELDS = ["frame.number", "frame.len", "ip.len", "ip.src", "ip.dst", "_ws.col.Protocol", "wg.type",
          "frame.time_relative"]


def read(pcap, decode_wg=False):
    """Return one dict per packet, using tshark -T fields."""
    cmd = ["tshark", "-r", str(pcap), "-T", "fields", "-E", "separator=\t"]
    if decode_wg:
        cmd += ["-d", "udp.port==51820,wg"]
    for f in FIELDS:
        cmd += ["-e", f]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        v = (line.split("\t") + [""] * len(FIELDS))[:len(FIELDS)]
        rows.append(dict(zip(FIELDS, v)))
    return rows


def summary(title, rows):
    print(f"\n=== {title} ===")
    if not rows:
        print("  empty")
        return
    sizes = [int(r["frame.len"]) for r in rows if r["frame.len"]]
    print(f"  packets: {len(rows)}   sizes: min {min(sizes)}, max {max(sizes)}, total {sum(sizes)} bytes")
    print("  protocols: " + ", ".join(f"{p} x{n}" for p, n in Counter(r["_ws.col.Protocol"] for r in rows).items()))
    print(f"  {'#':>3} {'time s':>8} {'len':>5}  {'src':<12} {'dst':<12} protocol / WireGuard type")
    for r in rows:
        wg = WG_TYPES.get(r["wg.type"].split(",")[0], "")
        print(f"  {r['frame.number']:>3} {float(r['frame.time_relative'] or 0):8.3f} {r['frame.len']:>5}  "
              f"{r['ip.src']:<12} {r['ip.dst']:<12} {r['_ws.col.Protocol']} {('- ' + wg) if wg else ''}")


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "exp7_results")
    c1 = read(d / "1_underlay_plain.pcap")
    c2 = read(d / "2_underlay_tunnel.pcap", decode_wg=True)
    c3 = read(d / "3_inner_wg0.pcap")
    summary("Capture 1: request in the clear, on the underlay", c1)
    summary("Capture 2: request through the tunnel, on the underlay", c2)
    summary("Capture 3: request through the tunnel, on wg0", c3)

    # Pair transport-data packets (outer) with inner packets, in order.
    outer = [int(r["ip.len"]) for r in c2 if r["wg.type"].split(",")[0] == "4" and r["ip.len"]]
    inner = [int(r["ip.len"]) for r in c3 if r["ip.len"]]
    print("\n=== Outer minus inner (what the tunnel adds per packet) ===")
    print(f"  transport-data packets on the underlay: {len(outer)}, packets on wg0: {len(inner)}")
    if len(outer) != len(inner):
        print("  NOTE: the counts differ, so only the first", min(len(outer), len(inner)), "are paired")
    print(f"  {'pair':>4} {'inner IP':>8} {'outer IP':>8} {'added':>6} = 20 IP + 8 UDP + 16 WG header + 16 tag + padding")
    for i, (o, n) in enumerate(zip(outer, inner), 1):
        print(f"  {i:>4} {n:>8} {o:>8} {o - n:>6}   padding = {o - n - 60}")
    if outer and inner:
        adds = sorted({o - n for o, n in zip(outer, inner)})
        print(f"  added bytes seen: {adds}  (60 fixed + 0-15 padding to a multiple of 16)")


if __name__ == "__main__":
    main()
