"""
collect_mac.py  -  Experiment 3, CSC 6410 (Sareena Challuri)

PURPOSE
  Read my Mac's network settings, save them as JSON in the shape of the
  course model (host_model.yang / host_schema.json), check them with
  jsonschema, and compare them with what I said they SHOULD be
  (intended.json). Prints a drift report: one line per difference.

HOW TO RUN (in Terminal, inside the "Experiment 3" folder)
  python3 collect_mac.py                      collect -> observed.json, validate, drift
  python3 collect_mac.py --out NAME.json      same, but save to another file
  python3 collect_mac.py --check FILE.json    only validate an existing file (for testing)

FILES IT WRITES
  observed.json           my real data (private - do NOT upload)
  observed_redacted.json  same data with MAC, IPv6 and host name hidden (safe for GitHub/report)

ASSUMPTIONS / LIMITS
  - macOS only. It uses ifconfig, networksetup, route and scutil.
  - Interface names are the BSD names (en0, lo0, bridge0...), because that is
    what ifconfig shows. The Settings window shows "Wi-Fi" instead.
  - If a command fails (for example no default route when Wi-Fi is off),
    the field is left out instead of crashing.
"""
import json
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).parent
SCHEMA_FILE = HERE / "Week03_demos" / "host_schema.json"
INTENDED_FILE = HERE / "intended.json"
TOP = "student-host:host"          # top-level name = "module-name:container" (RFC 7951)


def run(cmd):
    """Run a command and return its text output. Return "" if it fails."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def hardware_ports():
    """networksetup tells me which device is Wi-Fi, Ethernet, Thunderbolt Bridge...
    Returns {device: port name}, e.g. {"en0": "Wi-Fi"}."""
    ports, port = {}, None
    for line in run(["networksetup", "-listallhardwareports"]).splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port:
            ports[line.split(":", 1)[1].strip()] = port
    return ports


def guess_type(name, port):
    """Turn macOS names into the model's types: ethernet, wifi, loopback, virtual, other."""
    if name.startswith("lo"):
        return "loopback"
    if port == "Wi-Fi":
        return "wifi"
    if port and "Bridge" in port:
        return "virtual"           # Thunderbolt Bridge is a software bridge
    if port and ("Ethernet" in port or "LAN" in port):
        return "ethernet"
    if name.startswith(("utun", "awdl", "llw", "gif", "stf", "bridge", "anpi", "ap")):
        return "virtual"           # tunnels, AirDrop, VPN and other software interfaces
    return "other"


def mask_to_prefix(hex_mask):
    """ifconfig gives the mask as hex (0xffffff00). The model wants /24.
    Count the 1-bits: 0xffffff00 -> 24."""
    return bin(int(hex_mask, 16)).count("1")


def interfaces():
    """Read every interface from ifconfig and build one dict per interface."""
    ports = hardware_ports()
    result, cur, flags_up, status = [], None, False, None

    def finish():
        if cur is None:
            return
        # "status: active/inactive" is the best sign of up/down.
        # Interfaces with no status line (lo0, utun...) use the UP flag.
        if status is not None:
            cur["oper-status"] = "up" if status == "active" else "down"
        else:
            cur["oper-status"] = "up" if flags_up else "down"
        result.append(cur)

    for line in run(["ifconfig"]).splitlines():
        if line and not line[0].isspace():          # new interface starts, e.g. "en0: flags=..."
            finish()
            name = line.split(":", 1)[0]
            cur = {"name": name, "type": guess_type(name, ports.get(name))}
            flags = line.split("<", 1)[1].split(">", 1)[0].split(",") if "<" in line else []
            flags_up, status = "UP" in flags, None
            if " mtu " in line:
                cur["mtu"] = int(line.split(" mtu ")[1].split()[0])
            continue
        parts = line.split()
        if not parts or cur is None:
            continue
        if parts[0] == "ether":
            cur["mac"] = parts[1].lower()           # model wants lower case
        elif parts[0] == "inet" and "netmask" in parts:
            prefix = mask_to_prefix(parts[parts.index("netmask") + 1])
            cur.setdefault("ipv4", []).append(f"{parts[1]}/{prefix}")
        elif parts[0] == "inet6":
            addr = parts[1].split("%")[0]           # drop the "%en0" scope part
            plen = parts[parts.index("prefixlen") + 1] if "prefixlen" in parts else "128"
            cur.setdefault("ipv6", []).append(f"{addr}/{plen}")
        elif parts[0] == "status:":
            status = parts[1]
    finish()
    return result


def default_gateway():
    """route -n get default -> 'gateway: 192.168.1.1'. Empty if there is no default route."""
    for line in run(["route", "-n", "get", "default"]).splitlines():
        if line.strip().startswith("gateway:"):
            return line.split(":", 1)[1].strip()
    return None


def dns_servers():
    """scutil --dns lists 'nameserver[0] : 192.168.1.1'. Keep each server once, in order."""
    found = []
    for line in run(["scutil", "--dns"]).splitlines():
        line = line.strip()
        if line.startswith("nameserver["):
            server = line.split(":", 1)[1].strip()
            if server not in found:
                found.append(server)
    return found


def collect():
    """Build the whole observed configuration in the model's shape."""
    host = {
        "hostname": socket.gethostname(),
        "os": "macos",
        "collected-at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    gw = default_gateway()
    if gw:
        host["default-gateway"] = gw
    host["dns-server"] = dns_servers()
    host["interface"] = interfaces()
    return {TOP: host}


def schema_errors(data):
    """Check against host_schema.json. Print EVERY error with its path."""
    schema = json.loads(SCHEMA_FILE.read_text())
    v = Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}"
            for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))]


def drift(observed, intended):
    """Compare only the fields I wrote in intended.json. Match interfaces by name."""
    o, i, lines = observed.get(TOP, {}), intended.get(TOP, {}), []
    for field in ("hostname", "os", "default-gateway"):
        if field in i and i[field] != o.get(field):
            lines.append(f"host / {field}: intended {i[field]}, observed {o.get(field, 'MISSING')}")
    for server in i.get("dns-server", []):           # every intended DNS server must be there
        if server not in o.get("dns-server", []):
            lines.append(f"host / dns-server: intended {server}, observed MISSING")
    obs_if = {x["name"]: x for x in o.get("interface", [])}
    for want in i.get("interface", []):
        name = want["name"]
        have = obs_if.get(name)
        if have is None:
            lines.append(f"interface {name}: intended present, observed MISSING")
            continue
        for field, value in want.items():
            if field != "name" and have.get(field) != value:
                lines.append(f"interface {name} / {field}: intended {value}, observed {have.get(field, 'MISSING')}")
    return lines


def redact(data):
    """Copy for sharing: keep first 3 bytes of MAC, first 2 groups of IPv6, hide host name."""
    d = json.loads(json.dumps(data))
    h = d[TOP]
    h["hostname"] = "[redacted-host]"
    h["dns-server"] = [s if "." in s else s.split(":")[0] + ":" + s.split(":")[1] + ":xxxx" for s in h.get("dns-server", [])]
    for x in h.get("interface", []):
        if "mac" in x:
            x["mac"] = ":".join(x["mac"].split(":")[:3]) + ":xx:xx:xx"
        if "ipv6" in x:
            x["ipv6"] = [a.split(":")[0] + ":" + a.split(":")[1] + ":xxxx/" + a.split("/")[-1] for a in x["ipv6"]]
    return d


def report_validation(data, label):
    errs = schema_errors(data)
    print(f"\nValidation of {label} against host_schema.json: {'VALID' if not errs else str(len(errs)) + ' error(s)'}")
    for e in errs:
        print("   ", e)


if __name__ == "__main__":
    args = sys.argv[1:]

    # Test mode: only validate a file I give it (e.g. a copy I broke on purpose)
    if args[:1] == ["--check"]:
        report_validation(json.loads(Path(args[1]).read_text()), args[1])
        sys.exit(0)

    out = Path(args[1]) if args[:1] == ["--out"] else HERE / "observed.json"

    start = time.perf_counter()
    observed = collect()
    seconds = time.perf_counter() - start

    out.write_text(json.dumps(observed, indent=2))
    red_path = out.with_name(out.stem + "_redacted.json")
    red_path.write_text(json.dumps(redact(observed), indent=2))
    h = observed[TOP]
    print(f"Collected {len(h['interface'])} interfaces in {seconds:.2f} seconds")
    print(f"Saved {out.name} (private) and {red_path.name} (safe to share)")

    report_validation(observed, out.name)

    if INTENDED_FILE.exists():
        lines = drift(observed, json.loads(INTENDED_FILE.read_text()))
        print(f"\nDrift report (intended.json vs {out.name}): {len(lines)} difference(s)")
        for line in lines:
            print("   ", line)
    else:
        print("\nNo intended.json found, so no drift report.")
