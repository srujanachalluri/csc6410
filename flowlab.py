#!/usr/bin/env python3
# flowlab.py - Experiment 2: I am the controller.
# Builds 1 switch (s1) + 3 hosts (h1, h2, h3) with NO controller,
# then adds flow rules by hand with ovs-ofctl and prints the results.
# Run inside the lab container:  python3 -u flowlab.py 2>&1 | tee output.txt
# If it crashes halfway, run "mn -c" to clean up, then run it again.

import os
from functools import partial
from mininet.net import Mininet
from mininet.topo import SingleSwitchTopo
from mininet.node import OVSSwitch
from mininet.log import setLogLevel

# Pick the switch type from MN_SWITCH (userspace if no kernel module)
if 'datapath=user' in os.environ.get('MN_SWITCH', ''):
    Switch = partial(OVSSwitch, datapath='user')
else:
    Switch = OVSSwitch

setLogLevel('info')
# controller=None means no controller: the flow table starts empty
net = Mininet(topo=SingleSwitchTopo(3), switch=Switch, controller=None, autoSetMacs=True)
net.start()
s1 = net['s1']
h1, h2, h3 = net.get('h1', 'h2', 'h3')


def title(text):
    print('\n' + '=' * 60 + '\n' + text + '\n' + '=' * 60)


def sw(cmd):
    # run a command on the switch side and print it with its output
    print('$ ' + cmd)
    print(s1.cmd(cmd))


def ping(src, dst):
    # 3 pings, wait max 1 second for each reply
    print('$ %s ping -c 3 %s' % (src.name, dst.IP()))
    print(src.cmd('ping -c 3 -W 1 %s' % dst.IP()))


title('TEST 1 - Empty flow table (no rules, no controller)')
sw('ovs-vsctl get-fail-mode s1')
sw('ovs-ofctl show s1')
sw('ovs-ofctl dump-flows s1')
net.pingAll(timeout='1')

title('TEST 2 - My rules: ARP flood + one IP rule per host')
sw('ovs-ofctl add-flow s1 priority=100,arp,actions=flood')
for i in (1, 2, 3):
    sw('ovs-ofctl add-flow s1 priority=100,ip,nw_dst=10.0.0.%d,actions=output:%d' % (i, i))
net.pingAll(timeout='1')
sw('ovs-ofctl dump-flows s1')

title('TEST 3 - Drop h3 -> h1 at priority 200 (higher than 100)')
sw('ovs-ofctl add-flow s1 priority=200,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1,actions=drop')
ping(h3, h1)
ping(h2, h1)
sw('ovs-ofctl dump-flows s1')
sw('ovs-appctl ofproto/trace s1 in_port=3,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1')

title('TEST 4 - Same drop rule at priority 50 (lower than 100)')
sw('ovs-ofctl --strict del-flows s1 priority=200,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1')
sw('ovs-ofctl add-flow s1 priority=50,ip,nw_src=10.0.0.3,nw_dst=10.0.0.1,actions=drop')
ping(h3, h1)
sw('ovs-ofctl dump-flows s1')

title('TEST 5 - Table miss: ARP rule deleted, priority 0 drop rule')
sw('ovs-ofctl del-flows s1 arp')
for h in (h1, h2, h3):
    h.cmd('ip neigh flush all')   # clear ARP caches so hosts must ask again
sw('ovs-ofctl add-flow s1 priority=0,actions=drop')
net.pingAll(timeout='1')
sw('ovs-ofctl dump-flows s1')

title('TEST 6 - No rules, fail mode standalone')
sw('ovs-ofctl del-flows s1')
sw('ovs-vsctl set-fail-mode s1 standalone')
sw('ovs-ofctl dump-flows s1')
net.pingAll(timeout='1')

title('DONE')
net.stop()
