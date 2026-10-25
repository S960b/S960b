# Yaju OS: solving path

## 0. The whole plan

The challenge is a virtual BLE gadget. Port 6402 is not a shell and not even GATT
over TCP: it is a Rootcanal virtual Bluetooth controller speaking raw HCI/H4, so
the "reverse engineering" target is a whole wireless stack plus an ARM firmware.
The flag is not in a file - the emulator serves it one chunk at a time through a
virtual MMIO GPIO register. The plan was:

1. Read the shipped Python project and the 256 KiB nRF51422 flash image.
2. Understand the three runtime phases (app, maintenance, bootloader,
   uploaded app) and the HCI/TCP session model.
3. Reverse the hidden maintenance command from the flash bytes and use it to
   switch the device into its bootloader.
4. Drive the Nordic Legacy DFU to install an unsigned 176-byte Cortex-M0 Thumb
   application.
5. The payload reads the GPIO secret into SRAM and registers a GATT READ
   characteristic; the client reads it over ATT and takes the flag.

## 1. What the box actually is

Why this step: the transport and the memory model decide everything else.

`session_host.py` spawns an isolated Rootcanal + Qiling for each TCP connection,
so `nc` alone is useless; a host stack (Bumble) is required. The virtual device
is an nRF51, Cortex-M0 / ARMv6-M Thumb, BLE address `F6:F7:F8:F9:FA:FB`. The
firmware phases:

```text
TCP 6402 / HCI-H4 -- Bumble Host -- Rootcanal -- virtual nRF51
                                               |
              1. normal app -------> maintenance (hidden write command)
                     -------------------------> bootloader (Nordic legacy DFU)
                                          -----> our Thumb app at 0x1B000
                                                     | MMIO GPIO P0.IN
                                                     +-> GATT READ -> flag
```

The TCP/HCI session must stay open across both BLE resets: disconnecting during
them is normal (it is the device rebooting), but a new TCP connection would
create a different, clean Rootcanal/Qiling session and lose the DFU state.

## 2. The secret lives in MMIO, not in memory

Why this step: this rules out the obvious "dump the flag file" paths.

`hardware.py` defines:

```python
GPIO_P0_BASE = 0x5000_0000
GPIO_P0_IN = 0x5000_0000 + 0x510   # 0x50000510
```

`qiling_runtime.py` hooks `_SecretGpioP0`: every read of `P0.IN` returns the
next slice of the remote secret and advances an internal offset. The secret is
never pre-mapped into flash or SRAM, so custom ARM code must perform the MMIO
reads itself. The second half of the trick: the Qiling runtime only accepts
services that the loaded firmware registers through real S130 SVC calls, so the
payload must both read the GPIO and publish the result as a BLE attribute.

## 3. Phase 1: reverse the hidden maintenance command

Why this step: the shipped app firmware contains an obfuscated 7-byte command;
entering the bootloader requires sending it over GATT.

The maintenance runtime's `command_handler` (symbol at 0x1B008, i.e. base 0x1B000
+ 8) checks a 7-byte command with a byte-wise obfuscation. The bytes come straight
out of the flash image (`magic_proof.py`):

```python
code1 = flash[0x1b75b:0x1b762]   # 82 95 c1 37 4a b0 6e
code2 = flash[0x1b754:0x1b75b]   # 9d 8f 94 23 e8 a3 cd
for i in range(7):
    x = code1[i] ^ code2[i] ^ ((0x11 * i + 0x3d) & 0xff)
    b = ror8(x, (3 * i + 1) & 7)
```

resulting in the exact command:

```text
11 45 14 19 19 81 00   (= 11451419198100)
```

On receipt, the original ARM firmware writes `0xB1` into `POWER.GPREGRET`
(maintenance boot request) and the emulator restarts the device into the
bootloader. The GATT characteristic used to send it is
`5A1C0002-7C5D-4C8D-942C-A786C1D58B6D` (write).

## 4. Phase 2: Nordic legacy DFU with no signature

Why this step: the bootloader validates metadata and CRC but not a signature, so
an unsigned image is installable.

The bootloader in `second_stage_runtime.py` calls the real compiled
`prevalidate_metadata` / `postvalidate_image` functions - it does not accept any
byte stream. But this is old Nordic DFU: it checks the prepared metadata fields
and the image CRC16 and asks for no digital signature.

The init packet (14 bytes):

```python
crc = binascii.crc_hqx(image, 0xffff)
init = struct.pack('<HHIHHH', 0xffff, 0xffff, 0, 1, 0xfffe, crc)
```

For the payload (`176 = 0xB0` bytes, CRC16 = 0x11CC):

```text
init: ffffffff000000000100feffcc11
```

DFU sequence: enable notifications on the Control Point, `01 04` (start DFU,
application), packet `00 00 00 b0 00 00 00` (sizes), wait `10 01 01`, then
`02 00` + init bytes + `02 01` (init complete), `03` + image packets (20 bytes
each), `04` (validate), `05` (activate and reset). A small `--speed` delay keeps
the WRITE-WITHOUT-RESPONSE stream from overflowing the emulator.

## 5. Phase 3: the Cortex-M0 payload

Why this step: the uploaded application is the only thing that can touch MMIO
and register a characteristic.

`yaju_payload.S` / `yaju.ld` define a raw app at 0x1B000. First 8 bytes are the
vector table (MSP = 0x20007F00, reset = 0x1B009). The core loop:

```asm
ldr r0, =0x50000510      @ GPIO P0.IN
ldr r1, =0x20001000      @ SRAM buffer
movs r2, #64             @ 64 x 4-byte reads = 256 bytes
1:
    ldr r3, [r0]
    str r3, [r1]
    adds r1, #4
    subs r2, #1
    bne 1b
```

The emulator advances the secret offset on every 32-bit read; 256 bytes fits
the real flag. Then the app registers a primary service and a READ
characteristic through S130 SVCs:

- `svc #0x63` - `sd_ble_uuid_vs_add` (vendor UUID base)
- `svc #0xA0` - `sd_ble_gatts_service_add`
- `svc #0xA2` - `sd_ble_gatts_characteristic_add`

with properties 0x02 (read), value pointer at 0x20001000, max 256 bytes. The
resulting UUID is `66551235-3322-1100-F0EE-CCAA88664422`. The app spins forever
so the GATT table stays alive.

This is not an overflow in a classic DFU sense: the loader simply accepts an
unsigned controlled application and the Qiling runtime honestly executes its
GPIO reads and GATT registration.

## 6. Phase 4: reading the flag

Why this step: the secret only exits the device through the characteristic the
payload published.

After `CTRL 05` the client waits for the disconnect event, reconnects to the
same BLE address on the SAME HCI/TCP session, discovers the new GATT table, and
reads `66551235-3322-1100-F0EE-CCAA88664422` (ATT Read Blob follows for 256
bytes). Trailing NULs are stripped and the flag pattern is extracted. The real
remote log (`evidence/remote_success_user.log`) shows the flag string coming
out of the GPIO read.

## 7. Verification

Why this step: everything must be reproducible offline after the CTF is over.

`tools/verify_bundle.py` (stdlib-only) checks: original tar.gz and flash SHA-256,
the reverse key from the compiled flash, that the payload and solver match the
earlier exploit zip, DFU image length/alignment, the Cortex-M0 vector table, the
S130 SVC markers, image CRC16 and the init packet. Output ends with
`COMPLETE_PACKAGE_VERIFIED`. `tools/rebuild_payload.sh` rebuilds the firmware
with clang/lld/llvm-objcopy and byte-compares it (`REBUILD_EXACT_MATCH`).
`magic_proof.py` outputs `MAGIC_PROOF_PASS 11451419198100`.

## 8. Dead ends (do not repeat)

- **nc as a client.** Port 6402 is raw HCI/H4, not a chat line; a host stack is
  mandatory.
- **Dumping flash/SRAM for the flag.** The secret is generated on demand by
  MMIO reads, never pre-mapped; a file dump finds nothing.
- **Restarting the TCP connection between phases.** A new connection creates a
  fresh isolated session and the DFU state is lost; keep one HCI transport.
- **Signing the image.** There is no signature check in legacy DFU; prevalidate
  only needs valid metadata and CRC16.
- **A naive "read /flag.txt" from the app.** There is no filesystem reachable
  from the guest ARM code; the only exfil path is a registered GATT attribute.

## One-paragraph version

The virtual nRF51 serves BLE over an HCI/TCP socket; the flag is fed through a
virtual GPIO register. A hidden maintenance command reversed from the flash
switches the device into its bootloader; Nordic legacy DFU (no signature, only
CRC16 metadata) installs a 176-byte unsigned Thumb app that reads the GPIO
secret into SRAM and self-registers a GATT READ characteristic; the client reads
it over ATT in the same HCI session and extracts the flag.

## Lessons learned

- Raw transports (HCI over TCP) change the tooling: match the protocol with a
  real host stack, not a socket library.
- "Read the flag" in an emulator can be an MMIO side channel instead of a file;
  look at what hardware is hooked, not at what strings are in flash.
- A bootloader that checks CRC but not signatures is a code-execution primitive
  by design; a 176-byte Thumb payload that publishes data over the registered
  attribute is enough to exfiltrate anything MMIO exposes.