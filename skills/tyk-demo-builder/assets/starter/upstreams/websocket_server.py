import asyncio
import websockets


async def handler(socket):
    async for message in socket:
        await socket.send(message)


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8080):
        await asyncio.Future()


asyncio.run(main())
