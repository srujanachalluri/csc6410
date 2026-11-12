#!/usr/bin/env python3
"""Experiment 4 starter: a k = 4 fat-tree in Mininet with static ECMP (CSC 6410, Week 4).
Run in the lab container (or WSL with sudo -E):

    sudo -E python3 fattree_ecmp.py            # builds the fabric, installs the rules, opens the Mininet CLI
    sudo -E python3 fattree_ecmp.py --bw 10    # optional: limit every link to 10 Mb/s (TCLink)
    sudo -E python3 fattree_ecmp.py --hash     # optional: hash on IP + UDP/TCP ports with a different seed per switch
                                               #   (OVS selection_method=hash; needs OpenFlow 1.5 and Open vSwitch 2.4 or later)
The switch type comes from MN_SWITCH (kernel datapath "ovsk" or userspace "ovs,datapath=user"), like --switch $MN_SWITCH for mn.

No controller. Every switch gets static OpenFlow 1.3 rules (the Week 2 skill):
  * DOWN: match the destination prefix and send it toward the right pod / edge / host;
  * UP:   everything else goes to a SELECT group whose buckets are the uplinks — that is ECMP.
Open vSwitch picks a bucket by hashing the packet's header fields, so one flow stays on one path.
Names: host hPEH = pod P, edge switch E, host H, address 10.P.E.(H+2); edge ePE, aggregation aPA, core cIJ.
Fabric ports: edge 1..2 = hosts, 3..4 = agg 0..1; agg 1..2 = edge 0..1, 3..4 = core (A,0)..(A,1); core cIJ port P+1 = pod P.
This is a starting point: your own script reads the counters and does the analysis.
"""
import argparse
import os
from functools import partial
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink, Link
from mininet.topo import Topo
from mininet.cli import CLI
from mininet.log import setLogLevel, info

K = 4
H = K // 2                                       # hosts per edge = edges per pod = aggs per pod = uplinks per switch

# Open vSwitch datapath: the lab container sets MN_SWITCH from its lab check ("ovsk" = kernel, "ovs,datapath=user" = userspace).
# In WSL: export MN_SWITCH=ovsk (or ovs,datapath=user), then run with sudo -E so the setting reaches this script.
MN_SWITCH = os.environ.get("MN_SWITCH", "ovsk")
SWITCH = partial(OVSSwitch, datapath="user") if "datapath=user" in MN_SWITCH else OVSSwitch


class FatTree(Topo):
    def build(self, bw=None):
        opts = dict(bw=bw) if bw else {}
        n = 0
        def sw(name):
            nonlocal n
            n += 1
            return self.addSwitch(name, dpid='%016x' % n)
        core = {(i, j): sw(f'c{i}{j}') for i in range(H) for j in range(H)}
        for p in range(K):
            aggs = [sw(f'a{p}{a}') for a in range(H)]
            for e in range(H):
                edge = sw(f'e{p}{e}')
                for h in range(H):
                    host = self.addHost(f'h{p}{e}{h}', ip=f'10.{p}.{e}.{h + 2}/8')
                    self.addLink(host, edge, port2=1 + h, **opts)
                for a in range(H):
                    self.addLink(edge, aggs[a], port1=H + 1 + a, port2=1 + e, **opts)
            for a in range(H):
                for j in range(H):
                    self.addLink(aggs[a], core[(a, j)], port1=H + 1 + j, port2=1 + p, **opts)


def ofctl(sw, cmd, ver='OpenFlow13'):
    out = sw.cmd(f'ovs-ofctl -O {ver} {cmd}')
    if out.strip():
        info(f'*** {sw.name}: {out}')


def add_group(s, uplinks, hashed):
    if not hashed:                                # default: OVS picks the selection method (dp_hash in OVS 2.10 and later)
        ofctl(s, f'add-group {s.name} "group_id=1,type=select,{uplinks}"')
    else:                                         # explicit fields; selection_method_param = hash seed, different per switch
        seed = int(s.dpid, 16)
        ofctl(s, f'add-group {s.name} "group_id=1,type=select,selection_method=hash,'
                 f'fields(ip_src,ip_dst,udp_src,udp_dst,tcp_src,tcp_dst),selection_method_param={seed},{uplinks}"', 'OpenFlow15')


def install_rules(net, hashed=False):
    uplinks = ','.join(f'bucket=output:{H + 1 + u}' for u in range(H))
    for p in range(K):
        for e in range(H):
            s = net.get(f'e{p}{e}')
            for h in range(H):                    # down to a host on this edge
                ofctl(s, f'add-flow {s.name} "priority=200,ip,nw_dst=10.{p}.{e}.{h + 2},actions=output:{1 + h}"')
            add_group(s, uplinks, hashed)
            ofctl(s, f'add-flow {s.name} "priority=100,ip,actions=group:1"')        # up: ECMP over the aggs
        for a in range(H):
            s = net.get(f'a{p}{a}')
            for e in range(H):                    # down to an edge of this pod
                ofctl(s, f'add-flow {s.name} "priority=200,ip,nw_dst=10.{p}.{e}.0/24,actions=output:{1 + e}"')
            add_group(s, uplinks, hashed)
            ofctl(s, f'add-flow {s.name} "priority=100,ip,actions=group:1"')        # up: ECMP over the cores
    for i in range(H):
        for j in range(H):
            s = net.get(f'c{i}{j}')
            for p in range(K):                    # down to the right pod
                ofctl(s, f'add-flow {s.name} "priority=200,ip,nw_dst=10.{p}.0.0/16,actions=output:{1 + p}"')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bw', type=float, default=None, help='link rate in Mb/s (optional)')
    ap.add_argument('--hash', action='store_true', help='select groups hash on IP + ports, seeded per switch (OpenFlow 1.5)')
    args = ap.parse_args()
    setLogLevel('info')
    # failMode='secure': with no controller the switches use ONLY our rules (no flooding loops in a fabric full of cycles)
    switch = partial(SWITCH, failMode='secure', protocols='OpenFlow13,OpenFlow15')
    net = Mininet(topo=FatTree(bw=args.bw), switch=switch, controller=None, autoSetMacs=True,
                  link=TCLink if args.bw else Link)
    net.start()
    net.staticArp()                               # every host knows every MAC, so no ARP broadcasts are needed
    install_rules(net, args.hash)
    info(f'*** Switch type (MN_SWITCH): {MN_SWITCH}\n')
    info('*** Open vSwitch version: ' + net.switches[0].cmd('ovs-vsctl --version').splitlines()[0] + '\n')
    info('*** Core links leaving pod 0 (read these counters):\n')
    for a in range(H):
        for j in range(H):
            info(f'      a0{a}-eth{H + 1 + j}  ->  c{a}{j}\n')
    info('*** Try: pingall | h000 ping -c 3 10.3.0.2 | sh ovs-ofctl -O OpenFlow13 dump-ports a00\n')
    CLI(net)
    net.stop()


if __name__ == '__main__':
    main()
