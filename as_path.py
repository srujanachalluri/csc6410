"""
as_path.py - CSC 6410 Experiment 1
Turn a saved traceroute into a list of autonomous systems (ASes),
then compare it with the AS paths that BGP route collectors see.

Purpose:
    traceroute tells me which routers my packets pass through, but only by
    IP address. This script asks the RIPEstat Data API which AS announces
    each router's address, so I can see which networks (companies) I cross.

How to run:
    python3 as_path.py traceroute_cuw_run1.txt          # AS path only
    python3 as_path.py traceroute_cuw_run1.txt --bgp    # plus 3 BGP paths
    python3 as_path.py file.txt --bgp --dest 1.2.3.4   # if the header line is missing

Assumptions:
    - The input file is the raw text of "traceroute -n -m 30 <target>"
      (macOS/Linux) or "tracert -d -h 30 <target>" (Windows).
    - Each hop line starts with the hop number. I take the first IPv4
      address on the line. If the probes on one hop came back from
      different routers, I only use the first one.

Limits:
    - IPv4 only.
    - A hop that shows only * * * has no address, so it is skipped.
    - RIPEstat shows who ANNOUNCES the prefix in BGP. A router's interface
      can sit in a prefix owned by the neighbour network (for example at an
      exchange point), so one hop can be "credited" to the wrong AS.

Being polite to the API:
    Every answer is saved in rs_cache.json. If I run the script again, it
    reads the saved answer instead of asking again. New requests wait
    one second between each other.
"""

import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

API = "https://stat.ripe.net/data/{call}/data.json?resource={resource}"
CACHE_FILE = "rs_cache.json"

# These ranges are never announced in BGP, so there is nothing to look up.
# 100.64/10 is "shared" space that ISPs use for carrier-grade NAT.
NOT_IN_BGP = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
]

IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


# ---------- talking to RIPEstat (with a cache) ----------

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=1)


def ripestat(call, resource, cache):
    """Ask RIPEstat one question. Use the saved answer if I have one."""
    url = API.format(call=call, resource=resource)
    if url in cache:
        return cache[url]
    time.sleep(1)  # one request per second at most
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)["data"]
    except Exception:
        # Fallback 1: some Mac Python installs cannot check HTTPS
        # certificates. The built-in curl uses the Mac's own certificates.
        try:
            out = subprocess.run(["curl", "-s", "--max-time", "30", url],
                                 capture_output=True, text=True, check=True)
            data = json.loads(out.stdout)["data"]
        except Exception as err:
            # Fallback 2: do not crash the whole run because one lookup failed.
            print(f"  ! lookup failed for {resource}: {err}")
            return None
    cache[url] = data
    save_cache(cache)
    return data


def as_name(asn, cache):
    data = ripestat("as-overview", "AS" + asn, cache)
    return data.get("holder", "?") if data else "?"


# ---------- reading the traceroute file ----------

def read_traceroute(path):
    """Return (destination IP, list of (hop number, first IP or None))."""
    dest = None
    hops = []
    with open(path) as f:
        for line in f:
            # The header line ("traceroute to x (1.2.3.4), ...") is not a
            # hop. I only take the destination address from it.
            if line.lower().startswith(("traceroute to", "tracing route")):
                found = IPV4.search(line)
                if found:
                    dest = found.group(1)
                continue
            m = re.match(r"\s*(\d+)\s+(.*)", line)
            if not m:
                continue  # blank lines, "Trace complete." etc.
            ip = IPV4.search(m.group(2))
            hops.append((int(m.group(1)), ip.group(1) if ip else None))
    return dest, hops


def is_private(ip):
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in NOT_IN_BGP)


def redact(ip):
    """Hide the last two numbers, e.g. 73.45.12.9 -> 73.45.x.x"""
    a, b, _, _ = ip.split(".")
    return f"{a}.{b}.x.x"


# ---------- main work ----------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    want_bgp = "--bgp" in sys.argv
    cache = load_cache()

    dest, hops = read_traceroute(path)
    # traceroute prints its header line on stderr, so "| tee" alone does
    # not save it. If it is missing, I can give the destination by hand.
    if "--dest" in sys.argv:
        dest = sys.argv[sys.argv.index("--dest") + 1]
    print(f"File: {path}")
    print(f"Destination: {dest}\n")
    print(f"{'hop':>3}  {'address':<16} {'prefix':<19} {'AS':<8} name")

    as_path = []           # ASes in the order I meet them
    home_ip = None         # the first public hop gets redacted
    for num, ip in hops:
        if ip is None:
            print(f"{num:>3}  * * *  (no reply, nothing to look up)")
            continue
        if is_private(ip):
            print(f"{num:>3}  {ip:<16} private/shared - not in BGP, skipped")
            continue
        if home_ip is None:
            home_ip = ip
        # The same address can come back on several hops (it did in my
        # run 1), so hide it every time, not only the first time.
        shown = redact(ip) if ip == home_ip else ip
        info = ripestat("network-info", ip, cache)
        if info is None:
            print(f"{num:>3}  {shown:<16} lookup failed, skipped")
            continue
        asns = info.get("asns", [])
        if not asns:
            print(f"{num:>3}  {shown:<16} {'-':<19} {'none':<8} (no AS announces this)")
            continue
        asn = asns[0]
        print(f"{num:>3}  {shown:<16} {info.get('prefix', '-'):<19} "
              f"AS{asn:<6} {as_name(asn, cache)}")
        # Collapse repeats: only add the AS if it is different from the last.
        if not as_path or as_path[-1] != asn:
            as_path.append(asn)

    ip_hops = sum(1 for _, ip in hops if ip)
    print(f"\nIP hops that answered: {ip_hops} of {len(hops)}")
    print("AS path from traceroute: " + " -> ".join("AS" + a for a in as_path))
    print(f"AS hops: {len(as_path)}")
    for a in as_path:
        print(f"  AS{a:<7} {as_name(a, cache)}")

    if want_bgp and dest:
        compare_with_bgp(dest, as_path, cache)


def compare_with_bgp(dest, tr_path, cache):
    """Step 5: what do BGP route collectors see for the target's prefix?"""
    info = ripestat("network-info", dest, cache)
    if not info or not info.get("prefix"):
        print("\nNo BGP prefix found for the destination.")
        return
    prefix = info["prefix"]
    lg = ripestat("looking-glass", prefix, cache)
    print(f"\n--- BGP view for {prefix} (RIPEstat looking-glass) ---")
    if not lg:
        return

    # Take the first 3 different AS paths, from different collectors (RRCs)
    # if possible, so I do not show three copies of the same view.
    paths = []
    for rrc in lg.get("rrcs", []):
        for peer in rrc.get("peers", []):
            p = peer.get("as_path", "")
            if p and p not in [x[1] for x in paths]:
                paths.append((rrc.get("rrc", "?") + " " + rrc.get("location", ""), p))
                break
        if len(paths) == 3:
            break

    for where, p in paths:
        print(f"  {where}: {p}")

    if paths:
        origin = paths[0][1].split()[-1]  # the last AS on a BGP path is the origin
        tr_last = tr_path[-1] if tr_path else None
        print(f"\nOrigin AS in BGP: AS{origin} ({as_name(origin, cache)})")
        print(f"Last AS in my traceroute: AS{tr_last}")
        print("Same origin? " + ("YES" if origin == tr_last else "NO"))
        seen = set(a for _, p in paths for a in p.split())
        for a in tr_path:
            print(f"  AS{a:<7} appears in collector paths: {'yes' if a in seen else 'no'}")


if __name__ == "__main__":
    main()
