"""
WebSocket server for terminals using the WebSocket + JSON cloud protocol.

Terminals connect to this process (default port 7788, plain ws://), register
with their SN and are then served by ``WsSession``. Run it as its own
long-lived process. The protocol carries no credentials, so expose the port
only to the device network or terminate TLS/IP filtering in front of it.
"""

import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from device_gateway.transports import device_context
from device_gateway.transports.ws_json import WsSession

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0


@sync_to_async
def receive(session, message):
    close_old_connections()
    if session.device is not None:
        with device_context(session.device):
            return session.receive(message)
    replies = session.receive(message)
    return replies


@sync_to_async
def next_command(session):
    close_old_connections()
    with device_context(session.device):
        return session.next_command()


@sync_to_async
def close(session):
    close_old_connections()
    session.close()


async def pump(websocket, session):
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        if not session.registered:
            continue
        command = await next_command(session)
        if command:
            await websocket.send(json.dumps(command))


async def serve_connection(websocket):
    session = WsSession()
    pusher = asyncio.create_task(pump(websocket, session))
    try:
        async for raw in websocket:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            try:
                replies = await receive(session, message)
            except Exception:
                logger.exception("device_gateway: WS handler failed")
                await websocket.send(
                    json.dumps({"ret": message.get("cmd"), "result": False, "reason": 1})
                )
                continue
            for reply in replies:
                await websocket.send(json.dumps(reply))
    finally:
        pusher.cancel()
        if session.registered:
            await close(session)


class Command(BaseCommand):
    help = "Run the WebSocket server for WebSocket+JSON terminals."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=7788)

    def handle(self, *args, **opts):
        try:
            from websockets.asyncio.server import serve
        except ImportError as exc:
            raise CommandError("websockets is not installed") from exc

        async def main():
            async with serve(serve_connection, opts["host"], opts["port"], max_size=8 * 1024 * 1024):
                self.stdout.write(f"WebSocket gateway on ws://{opts['host']}:{opts['port']}")
                await asyncio.get_running_loop().create_future()

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
