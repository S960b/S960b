import socket, hashlib, re, time, sys

HOST = "<host>"
PORT = 13370
URL = sys.argv[1] if len(sys.argv) > 1 else ""

s = socket.create_connection((HOST, PORT), timeout=20)
s.settimeout(0.5)

def read_until(patterns, timeout=30):
    data = b""
    end = time.time() + timeout
    while time.time() < end:
        try:
            c = s.recv(65536)
        except socket.timeout:
            continue
        except Exception as e:
            print(f"\n[recv error {e}]")
            break
        if not c:
            print("\n[connection closed]")
            break
        data += c
        sys.stdout.write(c.decode(errors="replace"))
        sys.stdout.flush()
        if any(re.search(p, data) for p in patterns):
            break
    return data

banner = read_until([rb"Your Answer"], 20)
m = re.search(rb"Prefix: (\w+)", banner)
if not m:
    print("\n[!] no prefix")
    sys.exit(1)
prefix = m.group(1).decode()
print(f"\n[*] prefix={prefix}")
t0 = time.time()
i = 0
while True:
    if hashlib.sha256((prefix + str(i)).encode()).hexdigest().startswith("000000"):
        break
    i += 1
print(f"\n[*] pow answer={i} in {time.time()-t0:.1f}s")
s.sendall(str(i).encode() + b"\n")
read_until([rb"\(y/n\)"], 15)
s.sendall(b"y\n")
read_until([rb"url", rb"URL"], 15)
print(f"\n[*] url={URL}")
s.sendall((URL + "\n").encode())
read_until([rb"~ \$", rb"/ \$", rb"# "], 90)
time.sleep(1)
s.sendall(b"cat /proc/cmdline\n")
time.sleep(0.5)
s.sendall(b"uname -a; cat /proc/sys/kernel/randomize_va_space 2>/dev/null; cat /proc/sys/kernel/kptr_restrict 2>/dev/null\n")
read_until([rb"~ \$", rb"/ \$", rb"# ", rb"Kernel panic", rb"panic"], 45)
read_until([b"__never__"], 5)
print("\n[*] session end")
s.close()
