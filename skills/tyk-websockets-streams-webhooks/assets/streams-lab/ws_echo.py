"""WebSocket echo upstream for the lab. Send "headers" to get back the path and headers of the upgrade request."""
import asyncio
import json

import websockets


async def handler(socket):
    async for message in socket:
        if message == "headers":
            await socket.send(json.dumps({"path": socket.request.path, "headers": dict(socket.request.headers)}))
        else:
            await socket.send(message)


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8080):
        await asyncio.Future()


asyncio.run(main())
