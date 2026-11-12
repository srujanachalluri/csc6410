"""Class 1 demo: ECMP puts each flow on one path by hashing its 5-tuple (CSC 6410, Week 4)."""
import ipaddress, random, struct, zlib

PATHS = 4                                   # a leaf with 4 uplinks = 4 equal-cost paths through 4 spines

def path_for(src, dst, proto, sport, dport):
    key = (ipaddress.ip_address(src).packed + ipaddress.ip_address(dst).packed +
           bytes([proto]) + struct.pack("!HH", sport, dport))
    return zlib.crc32(key) % PATHS          # same 5-tuple -> same hash -> same path (no reordering inside a flow)

ports = [51514, 49731, 60212, 55001, 50388, 62950, 53127, 57844]   # random source ports chosen by the OS
load = [0] * PATHS
for sp in ports:
    p = path_for("10.20.101.11", "10.20.103.21", 6, sp, 443)
    load[p] += 1
    print(f"TCP 10.20.101.11:{sp} -> 10.20.103.21:443   path {p}")
print("flows per path:", load, f"  busiest share {max(load) / sum(load):.0%} (fair share {1 / PATHS:.0%})")

random.seed(1)                              # many random trials: how uneven is "random" hashing?
for m in (4, 8, 16, 32):
    trials = []
    for _ in range(20000):
        pick = random.choices(range(PATHS), k=m)          # each flow lands on a random path
        trials.append(max(pick.count(p) for p in range(PATHS)))
    print(f"{m:>2} flows on {PATHS} paths: busiest path carries {sum(trials) / len(trials) / m:.0%} of flows on average")
