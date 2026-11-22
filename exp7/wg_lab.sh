#!/usr/bin/env bash
# =============================================================================
# wg_lab.sh  -  CSC 6410 Experiment 7: What does a WireGuard tunnel hide, and
#               what does it still reveal?
# Author: Sareena Challuri
#
# PURPOSE
#   Build two endpoints as network namespaces ("red" and "blue") joined by a
#   veth pair (the "underlay", 192.0.2.1 <-> 192.0.2.2), add a WireGuard tunnel
#   between them (wg0, 10.99.0.1 <-> 10.99.0.2, UDP 51820), and capture the same
#   web request three ways:
#     (1) in the clear on the underlay
#     (2) through the tunnel, captured on the underlay (what an observer sees)
#     (3) through the tunnel, captured on wg0 (inside the tunnel)
#   Then find the largest ping that passes unfragmented (MTU probe).
#
# LOGIC
#   sudo bash wg_lab.sh all   = check -> up -> show -> capture -> show -> mtu ->
#                               views -> analyse -> down
#   Each part can also be run alone: check | up | show | capture | mtu | down
#
# ASSUMPTIONS
#   - Runs as root (sudo) in a GitHub Codespace terminal (Ubuntu 24.04) or in
#     the course lab container. Kernel WireGuard is needed (checked first).
#   - Nothing leaves this machine: both endpoints are namespaces on one host.
#
# SAFETY
#   - Private keys live in /root/wg-lab-keys (mode 700), never in the results
#     folder, never printed. "wg show" hides private keys. "down" deletes them.
#
# TESTING
#   - Every command is printed before its output and saved in
#     exp7_results/raw_output.txt for Appendix 2.
#
# LIMITS / FALLBACK
#   - If "WireGuard in kernel" is NO, the script stops and says so (the lab
#     sheet says to try the other path, then tell the instructor).
#   - If tshark does not decode WireGuard, it is forced with -d udp.port==51820,wg.
# =============================================================================
set -u
OUT="${OUT:-$PWD/exp7_results}"
KEYS=/root/wg-lab-keys
WEB=/tmp/wg-web
LAST=Challuri
mkdir -p "$OUT"
RAW="$OUT/raw_output.txt"

run() { echo; echo "\$ $*" | tee -a "$RAW"; eval "$@" 2>&1 | tee -a "$RAW"; }
say() { echo; echo "===== $* =====" | tee -a "$RAW"; }
R() { ip netns exec red "$@"; }    # run a command inside the red endpoint
B() { ip netns exec blue "$@"; }   # run a command inside the blue endpoint

tools() {
  # install what is missing (Codespace only; the lab container has them already)
  local need=""
  for t in wg tcpdump tshark curl ping; do command -v $t >/dev/null || need=1; done
  if [ -n "$need" ]; then
    echo "installing wireguard-tools tcpdump tshark curl iputils-ping ..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq wireguard-tools tcpdump tshark curl iputils-ping iproute2 >/dev/null 2>&1 \
      || echo "PROBLEM: apt-get install failed" | tee -a "$RAW"
  fi
}

check() {
  say "1. Lab check"
  run "date"
  run "uname -r"
  run "grep PRETTY_NAME /etc/os-release"
  run "echo CODESPACES=\${CODESPACES:-no}  in_container=\$([ -f /.dockerenv ] && echo yes || echo no)"
  run "wg --version"
  run "tcpdump --version 2>&1 | head -1"
  run "tshark --version 2>&1 | head -1"
  # The real test: can this kernel create a wireguard interface?
  ip link add wgcheck type wireguard 2>"$OUT/wgcheck.err" && WGK=yes || WGK=no
  ip link del wgcheck 2>/dev/null
  echo "WireGuard in kernel: $WGK $(cat "$OUT/wgcheck.err")" | tee -a "$RAW"
  if [ "$WGK" = no ]; then
    echo "PROBLEM: no kernel WireGuard here. Stop and screenshot this." | tee -a "$RAW"
    exit 1
  fi
}

up() {
  say "2. Build the endpoints"
  down quiet
  run "ip netns add red; ip netns add blue"
  run "ip link add veth-red type veth peer name veth-blue"
  run "ip link set veth-red netns red; ip link set veth-blue netns blue"
  R ip addr add 192.0.2.1/24 dev veth-red;  R ip link set veth-red up;  R ip link set lo up
  B ip addr add 192.0.2.2/24 dev veth-blue; B ip link set veth-blue up; B ip link set lo up
  # Keys: generated with umask 077 into /root/wg-lab-keys, never printed.
  mkdir -p "$KEYS"; chmod 700 "$KEYS"
  ( umask 077
    wg genkey | tee "$KEYS/red.key"  | wg pubkey > "$KEYS/red.pub"
    wg genkey | tee "$KEYS/blue.key" | wg pubkey > "$KEYS/blue.pub" )
  echo "keys created in $KEYS (private keys not shown)" | tee -a "$RAW"
  # wg0 in each endpoint; each side knows the other's public key and underlay address.
  R ip link add wg0 type wireguard
  R wg set wg0 private-key "$KEYS/red.key" listen-port 51820 \
       peer "$(cat $KEYS/blue.pub)" endpoint 192.0.2.2:51820 allowed-ips 10.99.0.2/32
  R ip addr add 10.99.0.1/24 dev wg0; R ip link set wg0 up
  B ip link add wg0 type wireguard
  B wg set wg0 private-key "$KEYS/blue.key" listen-port 51820 \
       peer "$(cat $KEYS/red.pub)" endpoint 192.0.2.1:51820 allowed-ips 10.99.0.1/32
  B ip addr add 10.99.0.2/24 dev wg0; B ip link set wg0 up
  run "R ip -br addr"
  run "B ip -br addr"
  # Web server on blue serving a file with my last name.
  mkdir -p "$WEB"; echo "Hello from $LAST - CSC6410 Experiment 7 test page" > "$WEB/secret.txt"
  B python3 -m http.server 8080 --directory "$WEB" >/dev/null 2>&1 &
  sleep 1
  echo "web server on blue (192.0.2.2 / 10.99.0.2) port 8080, file secret.txt" | tee -a "$RAW"
}

show() {
  say "wg show on both endpoints ($1)"
  run "R wg show"
  run "B wg show"
}

cap_start() { B tcpdump -U -n -i "$1" -w "$OUT/$2" >/dev/null 2>&1 & sleep 1; }
cap_stop()  { sleep 1; pkill -INT -f "tcpdump -U -n -i" ; sleep 1; }

capture() {
  say "3. Captures"
  echo "--- capture 1: request in the clear over the underlay (veth-blue)" | tee -a "$RAW"
  cap_start veth-blue 1_underlay_plain.pcap
  run "R curl -s http://192.0.2.2:8080/secret.txt"
  cap_stop
  echo "--- captures 2 and 3: request through the tunnel, on the underlay AND on wg0 at the same time" | tee -a "$RAW"
  cap_start veth-blue 2_underlay_tunnel.pcap
  cap_start wg0 3_inner_wg0.pcap
  run "R curl -s http://10.99.0.2:8080/secret.txt"
  cap_stop
  run "ls -l $OUT/*.pcap"
}

probe() {   # largest ping -M do payload that passes to $1
  local lo=1000 hi=1600 mid
  while [ $((hi - lo)) -gt 1 ]; do
    mid=$(( (lo + hi) / 2 ))
    if R ping -M do -c 1 -W 1 -s $mid "$1" >/dev/null 2>&1; then lo=$mid; else hi=$mid; fi
  done
  echo "$lo"
}
mtu() {
  say "4. MTU probe (Don't Fragment)"
  for dst in 10.99.0.2 192.0.2.2; do
    best=$(probe $dst)
    run "R ping -M do -c 1 -W 1 -s $best $dst"
    run "R ping -M do -c 1 -W 1 -s $((best + 1)) $dst"
    echo "RESULT $dst: largest payload $best bytes (IP packet $((best + 28)) bytes); $((best + 1)) fails" | tee -a "$RAW"
  done
  run "R ip link show wg0 | head -1"
  run "R ip link show veth-red | head -1"
}

views() {
  say "5. tshark views (Wireshark's command-line form)"
  run "tshark -r $OUT/1_underlay_plain.pcap -Y http -T fields -e frame.number -e ip.src -e ip.dst -e http.request.uri -e http.file_data"
  run "tshark -r $OUT/1_underlay_plain.pcap -Y 'http.response' -x | grep -i -B2 -A2 '$LAST' | head -20"
  run "tshark -r $OUT/2_underlay_tunnel.pcap -d udp.port==51820,wg -T fields -e frame.number -e frame.len -e ip.src -e ip.dst -e wg.type -e wg.receiver_index -e wg.counter"
  run "tshark -r $OUT/2_underlay_tunnel.pcap -x | grep -i '$LAST' || echo 'my last name does NOT appear anywhere in capture 2'"
  run "tshark -r $OUT/3_inner_wg0.pcap -Y http -T fields -e frame.number -e ip.src -e ip.dst -e http.request.uri -e http.file_data"
}

down() {
  [ "${1:-}" = quiet ] || say "6. Tear down"
  pkill -f "http.server 8080" 2>/dev/null
  ip netns del red 2>/dev/null; ip netns del blue 2>/dev/null
  rm -rf "$KEYS" "$WEB"
  [ "${1:-}" = quiet ] || echo "namespaces and lab keys deleted" | tee -a "$RAW"
}

case "${1:-all}" in
  all)
    : > "$RAW"; tools; check; up; show "before any traffic"; capture; show "after the captures"
    mtu; views
    say "7. Analysis script"
    run "python3 $(dirname "$0")/analyze_wg.py $OUT"
    down
    chown -R "${SUDO_UID:-0}:${SUDO_GID:-0}" "$OUT" 2>/dev/null
    echo; echo "Done. Results in $OUT (pcaps, raw_output.txt). Do not upload the .pcap files."
    ;;
  check) tools; check ;;  up) up ;;  show) show now ;;  capture) capture ;;  mtu) mtu ;;  down) down ;;
  *) echo "usage: sudo bash wg_lab.sh [all|check|up|show|capture|mtu|down]" ;;
esac
