#!/usr/bin/env bash
# =============================================================================
# exp6.sh  -  CSC 6410 Experiment 6: Where does a packet go between two containers?
# Author: Sareena Challuri
#
# PURPOSE
#   Build a small Docker network and find every part of the path a packet takes
#   between two containers: the container's eth0, the veth pair that joins it to
#   the host, the Linux bridge, and the NAT rule that publishes a port.
#
# LOGIC (what happens, in order)
#   0. Environment record: date, kernel, OS, docker version (Server section).
#   1. Clean up anything left from an earlier run.
#   2. Create a user-defined bridge network exp6net (10.66.6.0/24) and start:
#        ca, cb -> alpine:3.20 on exp6net (aliases a, b)
#        cc     -> alpine:3.20 on the DEFAULT bridge (to test name resolution)
#        web   -> nginx:1.27-alpine on exp6net, port 8080 on the host -> 80
#   3. Inside each container: ip addr, ip route and /sys/class/net/eth0/iflink.
#      iflink is the interface index of the OTHER end of the veth pair.
#   4. On the host side: find the interface whose index equals that iflink
#      (that is the host end of the veth) and the bridge it is attached to.
#   5. Pings by name and by IP (a->b, a->c, c->a), and a ping to the router
#      for comparison.
#   6. tcpdump on the exp6net bridge while a pings b (echo request + reply).
#   7. curl http://localhost:8080 and print the NAT rule that forwards it.
#   8. Print a summary table. Everything is also saved in exp6_results/.
#
# ASSUMPTIONS
#   - Runs in a GitHub Codespace terminal (Linux with Docker), as the normal
#     user with passwordless sudo. Not inside the course lab container.
#   - Docker runs its containers on THIS machine's network stack ("host view").
#     If the bridge and veths are not visible here (for example Docker Desktop,
#     or Docker running somewhere else), the script falls back to a helper
#     container that shares the Docker host's network namespace (--network host).
#
# TESTING
#   - Each step prints the exact command before its output, so the terminal
#     and raw_output.txt show what was run.
#   - The script checks that the host veth it found has the same index as the
#     container's iflink, and that its "master" is the expected bridge.
#
# LIMITS
#   - Ping times inside one machine are tiny and vary; 5 pings are a sample.
#   - The NAT rule may be in iptables (legacy or nft) or nftables; the script
#     prints all of them that exist and says which one held the rule.
#
# FALLBACK
#   - SUBNET can be changed if 10.66.6.0/24 clashes:  SUBNET=10.66.7.0/24 ./exp6.sh
#   - ./exp6.sh --cleanup removes everything this script created.
# =============================================================================
set -u
SUBNET="${SUBNET:-10.66.6.0/24}"
NET=exp6net
OUT=exp6_results
mkdir -p "$OUT"
RAW="$OUT/raw_output.txt"

# ----------------------------------------------------------------- helpers
# run: print the command (with "$ "), run it, and save both to raw_output.txt
run() { echo; echo "\$ $*" | tee -a "$RAW"; eval "$@" 2>&1 | tee -a "$RAW"; }
say() { echo; echo "===== $* =====" | tee -a "$RAW"; }

cleanup() {
  docker rm -f ca cb cc web exp6-helper >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
}
if [ "${1:-}" = "--cleanup" ]; then cleanup; echo "cleaned up"; exit 0; fi

: > "$RAW"   # start a fresh raw output file

# ----------------------------------------------------------------- 0. environment
say "0. Environment record"
run "date"
run "uname -r"
run "grep PRETTY_NAME /etc/os-release"
run "echo CODESPACES=\${CODESPACES:-no}"
run "docker version --format 'Client {{.Client.Version}} / Server {{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})'"
run "docker info --format 'Docker root: {{.DockerRootDir}}  Kernel: {{.KernelVersion}}  OS: {{.OperatingSystem}}'"

# ----------------------------------------------------------------- 1-2. build
say "1. Clean up old containers and network"
cleanup
say "2. Create the network and the containers"
run "docker network create --driver bridge --subnet $SUBNET $NET"
# ITERATION 1 (Nov 21): Docker 29 refused "--name a": "Invalid container name (a), only
# [a-zA-Z0-9][a-zA-Z0-9_.-] are allowed" - a name needs at least 2 characters. So the
# containers are now ca, cb, cc. On exp6net, a and b also get the network aliases "a"
# and "b", so the name test "ping b" still works. Aliases only exist on user-defined
# networks, so cc (default bridge) gets none.
run "docker run -d --name ca --network $NET --network-alias a alpine:3.20 sleep infinity"
run "docker run -d --name cb --network $NET --network-alias b alpine:3.20 sleep infinity"
run "docker run -d --name cc alpine:3.20 sleep infinity      # default bridge on purpose"
run "docker run -d --name web --network $NET -p 8080:80 nginx:1.27-alpine"
# alpine has ping (busybox) but no 'ip' with -d details; add iproute2 in a, b, c.
for n in ca cb cc; do
  echo "installing iproute2 in $n ..."
  docker exec "$n" apk add --no-cache -q iproute2 >/dev/null 2>&1 || echo "PROBLEM: apk add failed in $n"
done
run "docker network inspect $NET --format '{{json .IPAM.Config}}'"
run "docker network inspect $NET --format '{{range .Containers}}{{.Name}} {{.IPv4Address}} {{.MacAddress}}{{println}}{{end}}'"

# The Linux bridge for a user-defined network is br-<first 12 chars of the network id>.
BR="br-$(docker network inspect -f '{{.Id}}' $NET | cut -c1-12)"
echo "bridge for $NET is $BR" | tee -a "$RAW"

# ----------------------------------------------------------------- host view or helper?
# H runs a command in the Docker host's network namespace.
if ip link show "$BR" >/dev/null 2>&1; then
  MODE="direct (this machine is the Docker host)"
  H() { sudo sh -c "$*"; }
else
  MODE="helper container (--network host --pid host --privileged)"
  docker run -d --name exp6-helper --network host --pid host --privileged alpine:3.20 sleep infinity >/dev/null
  docker exec exp6-helper apk add --no-cache -q iproute2 tcpdump iptables nftables >/dev/null 2>&1
  H() { docker exec exp6-helper sh -c "$*"; }
fi
# tcpdump is needed on the host side; install it if missing (direct mode only).
if [ "${MODE#direct}" != "$MODE" ] && ! command -v tcpdump >/dev/null; then
  echo "tcpdump missing - installing it" | tee -a "$RAW"
  sudo apt-get update -qq >/dev/null 2>&1
  sudo apt-get install -y -qq tcpdump >/dev/null 2>&1 || echo "PROBLEM: could not install tcpdump" | tee -a "$RAW"
fi
echo "Host-side mode: $MODE" | tee -a "$RAW"

# ----------------------------------------------------------------- 3-4. inside + host side
say "3-4. Inside each container, and the matching host veth"
declare -A IP IFL VETH MASTER
for n in ca cb cc web; do
  if [ "$n" = web ]; then   # nginx image has no iproute2: use /proc and /sys instead
    run "docker exec web cat /sys/class/net/eth0/iflink"
    run "docker exec web hostname -i"
  else
    run "docker exec $n ip addr show eth0"
    run "docker exec $n ip route"
    run "docker exec $n cat /sys/class/net/eth0/iflink"
  fi
  IFL[$n]=$(docker exec $n cat /sys/class/net/eth0/iflink | tr -d '\r')
  IP[$n]=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $n)
  # Host side: the interface whose index is IFL is the host end of this veth.
  VETH[$n]=$(H "ip -o link" | awk -F': ' -v i="${IFL[$n]}" '$1==i {print $2}' | cut -d@ -f1)
  MASTER[$n]=$(H "ip -o link show dev ${VETH[$n]}" | grep -o 'master [^ ]*' | awk '{print $2}')
  run "H 'ip -d link show dev ${VETH[$n]}'"
  run "H 'bridge link show dev ${VETH[$n]}'"
  echo "CHECK $n: eth0 iflink=${IFL[$n]} -> host ${VETH[$n]} (index ${IFL[$n]}), master=${MASTER[$n]}" | tee -a "$RAW"
done
run "H 'bridge link'"
run "H 'ip -br addr show type bridge'"

# ----------------------------------------------------------------- 5. pings
say "5. Pings by name and by IP"
run "docker exec ca ping -c 5 b             # by alias on exp6net"
run "docker exec ca ping -c 5 cb            # by container name on exp6net"
run "docker exec ca ping -c 5 ${IP[cb]}"
run "docker exec ca ping -c 3 -W 1 cc       # name of a container on the DEFAULT bridge"
run "docker exec ca ping -c 3 -W 1 ${IP[cc]}   # IP of cc, other bridge"
run "docker exec cc ping -c 3 -W 1 ca       # from the default bridge, by name"
# ITERATION 2 (Nov 21): "ping: command not found" - the Codespace image has no ping.
command -v ping >/dev/null || { sudo apt-get install -y -qq iputils-ping >/dev/null 2>&1 || { sudo apt-get update -qq >/dev/null 2>&1; sudo apt-get install -y -qq iputils-ping >/dev/null 2>&1; }; }
GW=$(ip route | awk '/^default/ {print $3; exit}')
run "ping -c 5 $GW   # the Codespace's router, for comparison"

# ----------------------------------------------------------------- 6. tcpdump on the bridge
say "6. tcpdump on $BR while a pings b"
H "timeout 10 tcpdump -n -l -i $BR icmp -c 8" > "$OUT/tcpdump.txt" 2>&1 &
TD=$!
sleep 2
docker exec ca ping -c 4 b >/dev/null
wait $TD
echo "\$ tcpdump -n -i $BR icmp   (while: docker exec ca ping -c 4 b)" | tee -a "$RAW"
tee -a "$RAW" < "$OUT/tcpdump.txt"

# ----------------------------------------------------------------- 7. published port + NAT rule
say "7. Published port 8080 and the NAT rule"
run "curl -s -o /dev/null -w 'HTTP %{http_code} from localhost:8080\n' http://localhost:8080"
run "curl -s http://localhost:8080 | grep -o '<title>.*</title>'"
for t in iptables iptables-legacy iptables-nft; do
  if H "command -v $t" >/dev/null 2>&1; then
    run "H '$t -t nat -S 2>/dev/null | grep -E \"DNAT|8080|MASQUERADE\"'"
  fi
done
if H "command -v nft" >/dev/null 2>&1; then
  run "H 'nft list ruleset 2>/dev/null | grep -E \"dnat|8080|masquerade\"'"
fi

# ----------------------------------------------------------------- 8. summary
say "8. SUMMARY"
{
  printf "%-5s %-6s %-16s %-7s %-18s %-16s\n" container iface address iflink "host veth" bridge
  for n in ca cb cc web; do
    printf "%-5s %-6s %-16s %-7s %-18s %-16s\n" "$n" eth0 "${IP[$n]}" "${IFL[$n]}" "${VETH[$n]}" "${MASTER[$n]}"
  done
  echo "exp6net bridge: $BR   default bridge: docker0   host-side mode: $MODE"
} | tee -a "$RAW" "$OUT/summary.txt"
echo
echo "Saved: $RAW, $OUT/summary.txt, $OUT/tcpdump.txt"
echo "Clean up when finished:  ./exp6.sh --cleanup"
