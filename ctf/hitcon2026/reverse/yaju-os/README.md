# Yaju OS (reverse)

- **Platform:** HITCON CTF 2026
- **Category:** Reverse (embedded / BLE firmware exploitation)
- **Points / Solves:** 160 pts
- **Date:** 2026-10-24
- **Remote:** `<host>:6402` (raw HCI/H4 over TCP, not a shell)
- **Flag:** `hitcon{...}` (masked)
- **Files:** `assets/yaju-os-e0b330b2a3740d63.tar.gz` (original handout), `assets/board_flash.bin` (256 KiB nRF51422 firmware)
- **Description (official):** "My IOT homework_new folder（1）_ver.114514(final）"

An nRF51 (Cortex-M0) virtual device served over TCP as a Rootcanal BLE controller.
The remote flag is not in any file: the secret lives in a virtual MMIO GPIO
register that yields bytes on every read. The exploit chain:

1. Reverse the maintenance command hidden in the shipped flash (obfuscated bytes
   decode to `11 45 14 19 19 81 00`, i.e. `11451419198100`), write it to the
   vendor characteristic, and the device resets into its bootloader.
2. Feed the Nordic Legacy DFU: an init packet (device wildcards + CRC16) and the
   image, with no signature required; the bootloader accepts a 176-byte
   unsigned Cortex-M0 Thumb application.
3. The payload reads the GPIO secret into SRAM and self-registers a GATT READ
   characteristic via S130 SVCs.
4. Reconnect (same HCI/TCP session), find the characteristic
   `66551235-3322-1100-F0EE-CCAA88664422`, and read the flag.

A single TCP/HCI session must survive both BLE resets, because each TCP
connection spawns a fresh isolated Rootcanal/Qiling session.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/yaju_solve.py` - full BLE/HCI exploit (Bumble)
- `scripts/yaju_payload.S`, `scripts/yaju.ld` - the injected Cortex-M0 Thumb firmware
- `scripts/yaju_payload.bin` - ready 176-byte ARM firmware
- `scripts/magic_proof.py` - derives the maintenance key from the original flash
- `scripts/verify_bundle.py` - self-contained offline checks (stdlib only)
- `scripts/rebuild_payload.sh` - rebuild + byte-compare the payload
- `evidence/remote_success_user.log` - captured successful remote run
- `evidence/local_verifications.log` - repeatable offline checks