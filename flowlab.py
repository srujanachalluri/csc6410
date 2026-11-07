#!/usr/bin/env python3
# flowlab.py - Experiment 2, CSC 6410 (Sareena Challuri)
#
# Purpose: I am the controller. This script makes one switch (s1) and three
# hosts (h1, h2, h3) with no controller, so the flow table starts empty.
# Then it adds my rules with ovs-ofctl, one test at a time, and prints the
# rules and counters (dump-flows) before and after each ping test.
#
# How to run (inside the lab container, after ovs-vswitchd is running):
#   export MN_SWITCH=ovs,datapath=user
#   python3 -u flowlab.py 2>&1 | tee output.txt
#
# Assumptions: hosts are 10.0.0.1-3 on ports 1-3 (Mininet does this with
# a single switch), and Open vSwitch is already started.
# Limits: pings wait only 1 second for a reply, so a slow first reply can
# show as a lost ping (this happened once in my run, TEST 2).
# Fallback: if the script stops halfway, run "mn -c" to clean up, then run
# it again.

import os
from functools import partial
from mininet.net import Mininet
from mininet.topo import SingleSwitchTopo
from mininet.node import OVSSwitch
from mininet.log import setLogLevel

# Codespaces has no Open vSwitch kernel module, so I use the userspace
# switch when MN_SWITCH says datapath=user
if 'datapath=user' in os.environ.get('MN_SWITCH', ''):
    Switch = partial(OVSSwitch, datapath='user')
else:
    Switch = OVSSwitch

setLogLevel('info')
# controller=None -> no controller at all, so I have to add every rule myself
net = Mininet(topo=SingleSwitchTopo(3), switch=Switch, controller=None, autoSetMacs=True)
net.start()
s1 = net['s1']
h1, h2, h3 = net.get('h1', 'h2', 'h3')


def title(text):
    # prints a line of ===== so each test is easy to find in the output
    print('\n' + '=' * 60 + '\n' + text + '\n' + '=' * 60)


def sw(cmd):
    # runs an ovs command for the switch and shows the command + its answer
    print('$ ' + cmd)
    print(s1.cmd(cmd))


def ping(src, dst):
    # one host pings another 3 times, waiting at most 1 second per reply
    print('$ %s ping -c 3 %s' % (src.name, dst.IP()))
    print(src.cmd('ping -c 3 -W 1 %s' % dst.IP()))


# Test 1: nothing in the table yet, so I expect every ping to fail
title('TEST 1 - Empty flow table (no rules, no controller)')
sw('ovs-vsctl get-fail-mode s1')
sw('ovs-ofctl show s1')
sw('ovs-ofctl dump-flows s1')
net.pingAll(timeout='1')

# Test 2: the smallest set of rules - flood ARP, and send each IP to its port
title('TEST 2 - My rules: ARP flood + one IP rule per host')
sw('ovs-ofctl add-flow s1 priority=100,arp,actions=flood')
for i in (1, 2, 3):
    sw('ovs-ofctl add-flow s1 priority=100,ip,nw_dst=10.0.0.%d,actions=output:%d' % (i, i))
net.pingAll(timeout='1')
sw('ovs-ofctl dump-flows s1')

# Test 3: a drop rule ABOVE the forward rule - h3 to h1 should be blocked,
# h2 to h1 should still work. The trace shows which rule wins.
title('TEST 3 - Drop h3 -> h1 at priority 200 (higher than 100)')
sw('ovs-ofctl add-flow s1 priority=200,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1,actions=drop')
ping(h3, h1)
ping(h2, h1)
sw('ovs-ofctl dump-flows s1')
sw('ovs-appctl ofproto/trace s1 in_port=3,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1')

# Test 4: same drop rule but BELOW the forward rule - it should never count
title('TEST 4 - Same drop rule at priority 50 (lower than 100)')
sw('ovs-ofctl --strict del-flows s1 priority=200,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1')
sw('ovs-ofctl add-flow s1 priority=50,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1,actions=drop')
ping(h3, h1)
sw('ovs-ofctl dump-flows s1')

# Test 5: table miss - take away ARP and add a priority 0 drop rule so I can
# see (with its counter) the packets that match nothing else
title('TEST 5 - Table miss: ARP rule deleted, priority 0 drop rule')
sw('ovs-ofctl del-flows s1 arp')
for h in (h1, h2, h3):
    h.cmd('ip neigh flush all')   # hosts forget MAC addresses, must ask with ARP again
sw('ovs-ofctl add-flow s1 priority=0,actions=drop')
net.pingAll(timeout='1')
sw('ovs-ofctl dump-flows s1')

# Test 6: no rules, but standalone mode - the switch acts on its own
title('TEST 6 - No rules, fail mode standalone')
sw('ovs-ofctl del-flows s1')
sw('ovs-vsctl set-fail-mode s1 standalone')
sw('ovs-ofctl dump-flows s1')
net.pingAll(timeout='1')

title('DONE')
net.stop()
