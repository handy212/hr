"""
Bridge between an MQTT broker and the device gateway.

Subscribes to the terminals' upload topic, feeds each frame to the MQTT
transport and publishes whatever the dispatcher queues for online devices.
Run it as its own long-lived process next to the web workers.
"""

import logging
import os
import threading
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from device_gateway import services
from device_gateway.models import TerminalDevice
from device_gateway.transports import device_context
from device_gateway.transports import mqtt as transport

logger = logging.getLogger(__name__)

DEFAULT_UPLOAD_TOPIC = "/iot_hub/upload/{SN}"
DEFAULT_PUBLISH_TOPIC = "/iot_hub/publish/{SN}"


def env(name, default=""):
    return os.environ.get(f"DEVICE_GATEWAY_MQTT_{name}", default)


class Gateway:
    def __init__(self, client, upload_topic, publish_topic, byteorder):
        self.client = client
        self.prefix, _, self.suffix = upload_topic.partition("{SN}")
        self.publish_topic = publish_topic
        self.byteorder = byteorder
        self.dispatcher = transport.Dispatcher()
        self.lock = threading.Lock()

    def sn_from_topic(self, topic):
        if topic.startswith(self.prefix) and topic.endswith(self.suffix):
            return topic[len(self.prefix) : len(topic) - len(self.suffix) or None]
        return ""

    def publish(self, device, outbound):
        use_gzip = str((device.work_parameters or {}).get("MQTT_UseGZIP", 0)) == "1"
        frame = transport.encode_frame(
            outbound.cmd,
            outbound.body,
            outbound.cmd_id,
            outbound.file,
            use_gzip=use_gzip,
            byteorder=self.byteorder,
        )
        topic = self.publish_topic.replace("{SN}", device.sn)
        self.client.publish(topic, frame, qos=1)

    def on_message(self, client, userdata, msg):
        sn = self.sn_from_topic(msg.topic)
        try:
            message, attachment = transport.decode_frame(msg.payload, self.byteorder)
        except transport.FrameError as exc:
            logger.warning("device_gateway: bad frame on %s: %s", msg.topic, exc)
            return
        with self.lock:
            close_old_connections()
            device = services.find_gateway_device(sn, "mqtt")
            if device is None:
                logger.warning("device_gateway: unknown MQTT device %r", sn)
                return
            try:
                with device_context(device):
                    replies = transport.handle_message(device, message, attachment)
                for reply in replies:
                    self.publish(device, reply)
            except Exception:
                logger.exception("device_gateway: failed handling %s from %s",
                                 message.get("cmd"), sn)

    def tick(self):
        with self.lock:
            close_old_connections()
            devices = TerminalDevice.all_objects.filter(
                protocol="mqtt", is_active=True, is_online=True
            )
            for device in devices:
                try:
                    with device_context(device):
                        for outbound in self.dispatcher.collect(device):
                            self.publish(device, outbound)
                except Exception:
                    logger.exception("device_gateway: dispatch failed for %s", device.sn)


class Command(BaseCommand):
    help = "Run the MQTT bridge for terminals registered with the MQTT protocol."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=env("HOST", "127.0.0.1"))
        parser.add_argument("--port", type=int, default=int(env("PORT", "1883")))
        parser.add_argument("--username", default=env("USERNAME"))
        parser.add_argument("--password", default=env("PASSWORD"))
        parser.add_argument("--tls", action="store_true", default=env("TLS") == "1")
        parser.add_argument("--upload-topic", default=env("UPLOAD_TOPIC", DEFAULT_UPLOAD_TOPIC))
        parser.add_argument("--publish-topic", default=env("PUBLISH_TOPIC", DEFAULT_PUBLISH_TOPIC))
        parser.add_argument("--byte-order", choices=["little", "big"], default=env("BYTE_ORDER", "little"))
        parser.add_argument("--interval", type=float, default=2.0, help="Dispatch interval in seconds.")

    def handle(self, *args, **opts):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise CommandError("paho-mqtt is not installed") from exc
        for topic in (opts["upload_topic"], opts["publish_topic"]):
            if "{SN}" not in topic:
                raise CommandError(f"Topic {topic!r} must contain the {{SN}} mask")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if opts["username"]:
            client.username_pw_set(opts["username"], opts["password"])
        if opts["tls"]:
            client.tls_set()
        gateway = Gateway(client, opts["upload_topic"], opts["publish_topic"], opts["byte_order"])
        subscription = opts["upload_topic"].replace("{SN}", "+")

        def on_connect(client, userdata, flags, reason_code, properties=None):
            logger.info("device_gateway: MQTT connected (%s)", reason_code)
            client.subscribe(subscription, qos=1)

        client.on_connect = on_connect
        client.on_message = gateway.on_message
        client.connect(opts["host"], opts["port"], keepalive=60)
        client.loop_start()
        self.stdout.write(f"Listening on {subscription} @ {opts['host']}:{opts['port']}")
        try:
            while True:
                gateway.tick()
                time.sleep(opts["interval"])
        except KeyboardInterrupt:
            pass
        finally:
            client.loop_stop()
            client.disconnect()
