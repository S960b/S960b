import asyncio, json, sys
import websockets

HOST = sys.argv[1]  # instance host, for example <host>
DIRS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
CYCLE = [(0, 0), (1, 0), (1, -1), (2, -1), (2, -2), (3, -2), (4, -2), (4, -3), (4, -4),
         (3, -3), (3, -4), (2, -4), (2, -3), (1, -2), (1, -3), (1, -4), (0, -4), (-1, -3),
         (0, -3), (0, -2), (0, -1), (-1, -1), (-1, -2), (-2, -2), (-3, -1), (-2, -1), (-3, 0),
         (-4, 0), (-4, 1), (-4, 2), (-3, 1), (-2, 0), (-1, 0), (-2, 1), (-1, 1), (-2, 2),
         (-3, 2), (-4, 3), (-4, 4), (-3, 3), (-3, 4), (-2, 4), (-2, 3), (-1, 2), (-1, 3),
         (-1, 4), (0, 4), (1, 3), (0, 3), (0, 2), (1, 2), (2, 2), (3, 1), (4, 0), (4, -1),
         (3, -1), (3, 0), (2, 0), (2, 1), (1, 1), (0, 1)]
IDX = {c: i for i, c in enumerate(CYCLE)}
N = len(CYCLE)


def step_dir(head):
    i = IDX[head]
    q, r = CYCLE[(i + 1) % N]
    return DIRS.index((q - head[0], r - head[1]))


async def main():
    resets = 0
    async with websockets.connect(f"ws://{HOST}/ws", max_size=None) as ws:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            msg = json.loads(raw)
            if msg.get("type") != "state":
                print("MSG:", json.dumps(msg)[:500], flush=True)
                continue
            if msg.get("won"):
                print("WON len=%s" % msg.get("length"), flush=True)
                print("FLAG_MESSAGE:", msg.get("message"), flush=True)
                return
            if not msg.get("alive"):
                resets += 1
                print("dead len=%s food=%s reset#%d" % (msg.get("length"), msg.get("food"), resets), flush=True)
                if resets > 20:
                    return
                await ws.send(json.dumps({"type": "reset"}))
                continue
            head = (msg["snake"][0]["q"], msg["snake"][0]["r"])
            d = step_dir(head)
            print("len=%s head=%s food=%s dir=%d" % (msg.get("length"), head, msg.get("food"), d), flush=True)
            await ws.send(json.dumps({"type": "move", "dir": d}))


asyncio.run(main())
