import os
import sys
import json
import paho.mqtt.client as mqtt
from utils import CnzDefinitions

MQTT_DEFAULT_ADDRESS = "mosquitto-broker-host"
MQTT_DEFAULT_PORT =  1883
MQTT_DEFAULT_KEEPALIVE = 60
PROJECT_NAME = str(CnzDefinitions.PROJECT)


class MqttManager:
    def __init__(self, logger_mgr=None, config_data={}):
        self._logger_mgr = logger_mgr
        self.logger = self._logger_mgr.add_child_logger(self.__class__.__name__)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  userdata=None,
                                  protocol=mqtt.MQTTv5)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self._callbacks = []

        try:
            self.client.connect(config_data.get(str(CnzDefinitions.MQTT_HOST), MQTT_DEFAULT_ADDRESS),
                                config_data.get(str(CnzDefinitions.MQTT_PORT), MQTT_DEFAULT_PORT),
                                config_data.get(str(CnzDefinitions.MQTT_KEEPALIVE), MQTT_DEFAULT_KEEPALIVE),
                                clean_start=True)
        except Exception as e:
            self.logger.critical(f"Failed to connect to MQTT broker: {e.__class__.__name__}")
            sys.exit(-1)

    def on_connect(self, _client, _userdata, _flags, _rc, _properties):
        """Callback when connected"""
        self.client.subscribe(PROJECT_NAME + "/#")

        # Publish status and version automatically on connect
        self.client.publish(PROJECT_NAME + "/" + str(CnzDefinitions.STATUS),
                                 str(CnzDefinitions.ONLINE),
                                 retain=True)

    def on_message(self, _client, _userdata, msg):
        """Callback for received message"""
        msg_str = msg.payload.decode("utf-8")
        self.logger.debug("Message received: %s on topic: %s", msg_str, msg.topic)

    def loop(self):
        """MQTT Client loop forever"""
        self.client.loop_forever()

    def start(self):
        self.client.loop_start()

    def disconnect(self):
        self.client.publish(PROJECT_NAME + "/" + str(CnzDefinitions.STATUS),
                        str(CnzDefinitions.OFFLINE),
                        retain=True)
        self.client.disconnect()

    def message_callback_add(self, topic, callback):
        """Add a message callback for a specific topic"""
        if topic in self._callbacks:
            raise ValueError(f"Callback for topic {topic} already exists")
        self._callbacks.append(topic)
        self.client.message_callback_add(topic, callback)

    def callbacks(self):
        return self._callbacks


def test_callback(_client, _userdata, message):
    print(f"Custom handler: {message.topic} -> {message.payload}")


if __name__ == "__main__":
    mqtt_manager = MqttManager()
    mqtt_manager.message_callback_add("mqttmanager/test", test_callback)
    try:
        mqtt_manager.loop()
    except KeyboardInterrupt:
        print("Shutting down...")
        mqtt_manager.disconnect()