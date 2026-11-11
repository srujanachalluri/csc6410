"""
core_counters.py  -  Experiment 4, CSC 6410 (Sareena Challuri)

PURPOSE
  Measure how ECMP splits my flows over the 4 core links that leave pod 0
  of the k=4 fat-tree:  a00-eth3 (to c00), a00-eth4 (to c01),
                        a01-eth3 (to c10), a01-eth4 (to c11).
  I read the transmit-byte counter of each link BEFORE and AFTER one iperf
  run, and the difference is the traffic of that run on that link.

HOW I USE IT (inside the Mininet CLI started by fattree_ecmp.py)
  sh python3 core_counters.py start                 # reading BEFORE the run
  h000 iperf -c 10.3.0.2 -u -b 1M -P 16 -t 20       # the traffic
  sh python3 core_counters.py end 16 1               # reading AFTER: N=16, run 1 (or "fail")
  sh python3 core_counters.py summary                # table of all runs so far

LOGIC
  - Counters never reset by themselves, so one reading alone means nothing.
    "start" saves the 4 counters to before.json; "end" reads them again,
    subtracts, and prints bytes and share (%) per link.
  - share = bytes on the link / bytes on all 4 links.
  - max/mean = busiest link's bytes / average bytes of the 4 links.
    1.00 means a perfect even split; 4.00 means everything on one link.
  - Every "end" is also added to results.csv, so "summary" can print
    the three runs of each N together.

ASSUMPTIONS
  - All Mininet switch ports are normal Linux interfaces in the same
    container, so /sys/class/net/<port>/statistics/tx_bytes exists.
  - Port numbers 3 and 4 on a00/a01 are the core links (the starter prints this).

TESTING
  - With no traffic, start + end should show (almost) 0 bytes everywhere.
  - With N=1 flow, almost all bytes should be on ONE link.

LIMITS
  - ARP and other small control frames are counted too ("noise"). They are
    tiny next to 20 s of 1 Mbps UDP per flow (about 2.5 MB per flow).
  - Bytes are not flows: two flows on one link look like twice the bytes.

FALLBACK
  - If the /sys file is missing, I read the same counter with
    ovs-ofctl -O OpenFlow13 dump-ports <switch> <port>.
"""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
BEFORE = HERE / "before.json"
RESULTS = HERE / "results.csv"

# The 4 core-facing ports of pod 0: (interface name, switch, port number, core switch)
LINKS = [("a00-eth3", "a00", 3, "c00"),
         ("a00-eth4", "a00", 4, "c01"),
         ("a01-eth3", "a01", 3, "c10"),
         ("a01-eth4", "a01", 4, "c11")]


def tx_bytes(ifname, switch, port):
    """Bytes sent so far on one link. First try Linux /sys, then ovs-ofctl."""
    f = Path(f"/sys/class/net/{ifname}/statistics/tx_bytes")
    if f.exists():
        return int(f.read_text())
    # Fallback: ask Open vSwitch for this port's counters
    out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-ports", switch, str(port)],
                         capture_output=True, text=True).stdout
    m = re.search(r"tx pkts=\d+, bytes=(\d+)", out)
    if not m:
        sys.exit(f"Cannot read counter for {ifname}. Is the fat-tree running?")
    return int(m.group(1))


def read_all():
    return {name: tx_bytes(name, sw, port) for name, sw, port, _ in LINKS}


def start():
    BEFORE.write_text(json.dumps(read_all()))
    print("Saved the BEFORE reading of the 4 core links.")


def end(n, run):
    if not BEFORE.exists():
        sys.exit("Run 'start' first.")
    before, after = json.loads(BEFORE.read_text()), read_all()
    diff = {name: after[name] - before[name] for name in after}
    total = sum(diff.values()) or 1          # avoid dividing by 0 if no traffic
    mean = total / len(diff)
    busiest = max(diff, key=diff.get)

    print(f"\nN = {n} flows, run {run}")
    print(f"{'link':<10}{'to':<5}{'bytes':>14}{'share':>9}")
    for name, _, _, core in LINKS:
        print(f"{name:<10}{core:<5}{diff[name]:>14,}{diff[name] / total:>9.1%}")
    print(f"{'total':<15}{total:>14,}")
    print(f"busiest = {busiest} ({diff[busiest] / total:.1%}),  max/mean = {diff[busiest] / mean:.2f}")

    new = not RESULTS.exists()
    with RESULTS.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["N", "run"] + [l[0] for l in LINKS] + ["busiest_share", "max_mean"])
        w.writerow([n, run] + [diff[l[0]] for l in LINKS]
                   + [round(diff[busiest] / total, 3), round(diff[busiest] / mean, 2)])


def summary():
    if not RESULTS.exists():
        sys.exit("No results yet.")
    rows = list(csv.DictReader(RESULTS.open()))
    print(f"{'N':>3} {'run':>4}  " + "  ".join(f"{l[0]:>9}" for l in LINKS) + "   busiest  max/mean")
    for r in rows:
        total = sum(int(r[l[0]]) for l in LINKS) or 1
        shares = "  ".join(f"{int(r[l[0]]) / total:>9.1%}" for l in LINKS)
        print(f"{r['N']:>3} {r['run']:>4}  {shares}   {float(r['busiest_share']):>6.1%}  {r['max_mean']:>8}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "start":
        start()
    elif cmd == "end" and len(sys.argv) == 4:
        end(int(sys.argv[2]), sys.argv[3])
    elif cmd == "summary":
        summary()
    else:
        print(__doc__)
