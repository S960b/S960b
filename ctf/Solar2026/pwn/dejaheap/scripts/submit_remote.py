import os, socket, sys, time
HOST = os.environ.get('DH_HOST', '<host>')
PORT = int(os.environ.get('DH_PORT', '0'))
def run(vmbytes, timeout=12):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    s.settimeout(timeout)
    try:
        b = s.recv(512)
    except socket.timeout:
        b = b''
    s.sendall(vmbytes)
    try:
        s.shutdown(socket.SHUT_WR)
    except OSError:
        pass
    out = bytearray()
    while True:
        try:
            ch = s.recv(8192)
        except socket.timeout:
            break
        if not ch:
            break
        out.extend(ch)
        if len(out) > 1 << 20:
            break
    s.close()
    return bytes(b) + bytes(out)
if __name__ == '__main__':
    vm = sys.argv[1]
    data = open(vm, 'rb').read()
    r = run(data)
    print('OUTPUT', repr(r[:6000]))
    print('HEX', r[:2000].hex(' '))
