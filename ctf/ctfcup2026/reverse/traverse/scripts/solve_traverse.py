#!/usr/bin/env python3
"""Solver for the CTF Cup 2026 'traverse' FPGA challenge.

Model of the gate-level netlist produced by
    iceunpack traverse.bin chip.asc && icebox_vlog -s -p chip.pcf chip.asc > chip.v

The design is an 8-bit finite state machine driven by the UART receiver:
  * every received character c advances the state:  s' = F(s, c)
  * a newline produces the answer byte from F(s, '\\n')  ->  'f' = fail, 's' = success
Reachable state graph is small (139 states), so BFS from the reset state
finds the unique character sequence that lands on the accepting state.
"""
import re
import sys

NEXT = ['n1263', 'n769', 'n1207', 'n1139', 'n1420', 'n1027', 'n1398', 'n1200']
STATE = ['n436', 'n439', 'n440', 'n441', 'n530', 'n531', 'n532', 'n534']
RX = {'n54': 0, 'n56': 1, 'n58': 2, 'n55': 3, 'n51': 4, 'n57': 5, 'n53': 6, 'n52': 7}
REPLY = ['n892', 'n1138', 'n1171']
RESET_STATE = 0xA0
OK_REPLY = (1, 1, 0)


class P:
    TOK = re.compile(r"\s*(1'b[01]|\d+|[A-Za-z_]\w*|[!&|?:()])")

    def __init__(self, s):
        self.t = []
        i = 0
        while i < len(s):
            m = self.TOK.match(s, i)
            if not m:
                if s[i:].strip() == '':
                    break
                raise ValueError(s[i:i + 20])
            self.t.append(m.group(1))
            i = m.end()
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def node(self):
        c = self.orx()
        if self.peek() == '?':
            self.i += 1
            a = self.node()
            assert self.t[self.i] == ':'
            self.i += 1
            b = self.node()
            return ('tern', c, a, b)
        return c

    def orx(self):
        n = self.andx()
        while self.peek() == '|':
            self.i += 1
            n = ('or', n, self.andx())
        return n

    def andx(self):
        n = self.un()
        while self.peek() == '&':
            self.i += 1
            n = ('and', n, self.un())
        return n

    def un(self):
        t = self.peek()
        if t == '!':
            self.i += 1
            return ('not', self.un())
        if t == '(':
            self.i += 1
            e = self.node()
            self.i += 1
            return e
        self.i += 1
        if t in ("1'b0", '0'):
            return ('c', 0)
        if t in ("1'b1", '1'):
            return ('c', 1)
        return ('v', t)


def load_netlist(path):
    src = open(path).read()
    nets = {}
    for m in re.finditer(r'assign (\w+)\s*=\s*(.*?);', src, re.S):
        expr = re.sub(r'/\*.*?\*/', '', m.group(2)).strip()
        nets[m.group(1)] = P(expr).node()
    return nets


def ev(net, env, memo, nets):
    if net in memo:
        return memo[net]
    if net in env:
        return env[net]
    node = nets[net]

    def w(nd):
        k = nd[0]
        if k == 'c':
            return nd[1]
        if k == 'v':
            return ev(nd[1], env, memo, nets)
        if k == 'not':
            return 1 - w(nd[1])
        if k == 'and':
            return w(nd[1]) & w(nd[2])
        if k == 'or':
            return w(nd[1]) | w(nd[2])
        return w(nd[2]) if w(nd[1]) else w(nd[3])

    memo[net] = w(node)
    return memo[net]


def make_env(state, char):
    e = {n: (char >> b) & 1 for n, b in RX.items()}
    for i, n in enumerate(STATE):
        e[n] = (state >> i) & 1
    return e


def step(nets, state, char):
    env = make_env(state, char)
    memo = {}
    out = 0
    for i, n in enumerate(NEXT):
        out |= ev(n, env, memo, nets) << i
    return out


def reply_bits(nets, state, char=0x0A):
    env = make_env(state, char)
    memo = {}
    return tuple(ev(n, env, memo, nets) for n in REPLY)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'chip.v'
    nets = load_netlist(path)
    seen = {RESET_STATE: ''}
    queue = [RESET_STATE]
    answer = None
    while queue:
        nxt = []
        for s in queue:
            for n in (NEXT + REPLY):
                if n not in nets:
                    raise SystemExit('unexpected netlist: missing %s' % n)
            for c in range(256):
                t = step(nets, s, c)
                if t not in seen:
                    seen[t] = seen[s] + chr(c)
                    nxt.append(t)
                    if reply_bits(nets, t) == OK_REPLY:
                        answer = seen[t]
        queue = nxt
    print('reachable states:', len(seen))
    if answer:
        print('FLAG:', answer)
    else:
        print('no accepting state reachable')


if __name__ == '__main__':
    main()
