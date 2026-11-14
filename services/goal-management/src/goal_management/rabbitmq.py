"""RabbitMQ publisher with persistent messages and synchronous confirms."""
from __future__ import annotations
import json
import pika

class ConfirmingPublisher:
    def __init__(self, url:str):
        self.connection=pika.BlockingConnection(pika.URLParameters(url)); self.channel=self.connection.channel()
        self.channel.confirm_delivery()
    def publish_confirmed(self,routing_key:str,body:dict)->None:
        ok=self.channel.basic_publish('goalpulse.events',routing_key,json.dumps(body),pika.BasicProperties(delivery_mode=2,content_type='application/json'),mandatory=True)
        if not ok: raise RuntimeError('publisher confirm failed')
