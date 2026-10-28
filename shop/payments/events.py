"""Publishes business events. Uses RabbitMQ when RABBITMQ_URL is set (Docker), otherwise a direct HTTP call."""
import os, json, uuid, httpx
RABBIT = os.getenv("RABBITMQ_URL", ""); NOTIFY = os.getenv("NOTIFY_URL", "http://127.0.0.1:8009"); KEY = os.getenv("INTERNAL_KEY", "internal")
def publish(kind, **data):
    ev = {"event_id": uuid.uuid4().hex, "kind": kind, **data}
    try:
        if RABBIT:
            import pika
            c = pika.BlockingConnection(pika.URLParameters(RABBIT)); ch = c.channel(); ch.queue_declare("notifications", durable=True)
            ch.basic_publish("", "notifications", json.dumps(ev), pika.BasicProperties(delivery_mode=2)); c.close()
        else: httpx.post(f"{NOTIFY}/events", json=ev, headers={"X-Internal-Key": KEY}, timeout=3)
    except Exception: pass  # a notification problem must never break an order
