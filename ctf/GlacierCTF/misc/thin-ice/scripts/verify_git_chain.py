#!/usr/bin/env python3
"""Verify thin-ice commit->tree->blob IDs offline, without embedding the flag.

Usage:
  python3 verify_git_chain.py 'gctf{YOUR_PRIVATE_FLAG}'
"""
import hashlib
import sys

EXPECTED_BLOB = "5c5955cf03e044090e9db2f2280fe08acdb1c5e1"
EXPECTED_TREE = "6fe60ef3d25dd3e8bccec929a4b44297e80e41a0"
EXPECTED_COMMIT = "2c7c30925427d63273b2fb14c7b81c6fe4197074"


def git_oid(kind: str, content: bytes) -> str:
    return hashlib.sha1(kind.encode() + b" " + str(len(content)).encode()
                        + b"\0" + content).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_git_chain.py 'gctf{PRIVATE_VALUE}'")
    flag = sys.argv[1].encode()
    blob = git_oid("blob", flag)
    tree_content = b"100644 flag.txt\0" + bytes.fromhex(blob)
    tree = git_oid("tree", tree_content)
    commit = (b"tree " + tree.encode() + b"\n"
              b"author losfuzzys <mail@losfuzzys.net> 1096848000 +0000\n"
              b"committer losfuzzys <mail@losfuzzys.net> 1096848000 +0000\n"
              b"\ntest.\n")
    head = git_oid("commit", commit)
    print("blob:  ", blob, "MATCH" if blob == EXPECTED_BLOB else "MISMATCH")
    print("tree:  ", tree, "MATCH" if tree == EXPECTED_TREE else "MISMATCH")
    print("commit:", head, "MATCH" if head == EXPECTED_COMMIT else "MISMATCH")
    print("commit body:", len(commit), "bytes")
    if (blob, tree, head) != (EXPECTED_BLOB, EXPECTED_TREE, EXPECTED_COMMIT):
        raise SystemExit("Git object chain does not match the remote advertisement")
    print("ALL THREE OBJECT IDS MATCH")


if __name__ == "__main__":
    main()
