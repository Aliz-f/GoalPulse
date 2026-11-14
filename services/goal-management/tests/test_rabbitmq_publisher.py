from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.rabbitmq import ConfirmingPublisher


EVENT = {
    "event_id": "ae000000-0000-4000-8000-000000000009",
    "event_type": "goal.activated.v1",
    "correlation_id": "ae000000-0000-4000-8000-000000000002",
    "payload": {"goal_id": "ae000000-0000-4000-8000-000000000002", "goal_version": 1},
}


class Channel:
    is_open = True

    def __init__(self):
        self.confirmed = False
        self.published = []

    def confirm_delivery(self):
        self.confirmed = True

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return True


class Connection:
    is_open = True

    def __init__(self, _parameters):
        self.channel_instance = Channel()

    def channel(self):
        return self.channel_instance

    def close(self):
        self.is_open = False


def test_publisher_uses_frozen_exchange_route_persistence_and_confirms():
    connections = []

    def factory(parameters):
        connection = Connection(parameters)
        connections.append(connection)
        return connection

    publisher = ConfirmingPublisher("amqp://guest:guest@rabbitmq/", connection_factory=factory)
    publisher.publish_confirmed("goal.activated.v1", EVENT)
    channel = connections[0].channel_instance
    assert channel.confirmed is True
    call = channel.published[0]
    assert call["exchange"] == "goalpulse.events"
    assert call["routing_key"] == "goal.activated.v1"
    assert call["mandatory"] is True
    assert call["properties"].delivery_mode == 2
    assert call["properties"].message_id == EVENT["event_id"]
