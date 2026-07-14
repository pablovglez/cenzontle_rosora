import enum

# General Definitions

class CnzDefinitions(enum.StrEnum):
    # Project name
    PROJECT = "rosora"
    CENZONTLE = "cenzontle"

    # Config-related
    UUID_DEVICE_LIST = "uuid_device_list"
    MQTT_HOST = "mqtt_host"
    MQTT_PORT = "mqtt_port"
    MQTT_KEEPALIVE = "mqtt_keepalive"
    PASSKEY = "passkey"
    NO_BLOCK_TIMEOUT = "no_block_timeout"

    # MQTT-related
    STATUS = "status"
    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    COMMAND = "command"
    ENABLED = "enabled"
    OUTPUT = "output"
    AMBIENT = "ambient"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    LUX = "lux"
    DEVICE_TOPIC = PROJECT + "/" + CENZONTLE
    DEVICE_CMD_TOPIC = PROJECT + "/" + CENZONTLE+ "/" + COMMAND

    # BLE-related
    DEVICE_NAME = "device_name"
    COMMAND_KEY = "command_key"
    ARGS = "args"
    PAYLOAD = "payload"

    ECHO = "echo"
    SET_RELAY = "set_relay"
    RELAY_NUMBER = "relay_number"
    RELAY_STATE = "relay_state"

    def __str__(self):
        return self.value


class NotificationsEnum(enum.IntEnum):
    NTFY_ACK = 1,
    NTFY_ECHO = 2,
    NTFY_RESPONSE = 11, #3
    NTFY_AMBIENT = 4
