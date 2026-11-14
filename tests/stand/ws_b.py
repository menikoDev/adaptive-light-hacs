"""One websocket command against stand B (needs aiohttp: run with the harness venv, /tmp/hatest/venv/bin/python).
usage: ws_b.py '<json message without id>'   prints the JSON result"""
import asyncio, json, sys

import aiohttp

PORT, TOK = 18125, open("/tmp/hatest_int/token").read().strip()


async def main(msg):
    async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{PORT}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": TOK})
        assert (await ws.receive_json())["type"] == "auth_ok"
        await ws.send_json({"id": 1, **msg})
        r = await ws.receive_json()
        print(json.dumps(r))


asyncio.run(main(json.loads(sys.argv[1])))
