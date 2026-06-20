from utils import CnzDefinitions


config_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        str(CnzDefinitions.UUID_DEVICE_LIST): {
            "type": "array",
            "items": {
                "type": "string",
                "description": "UUID of the device"
            },
        },
        str(CnzDefinitions.MQTT_HOST): {
            "type": "string",
            "description": "MQTT broker host"
        },
        str(CnzDefinitions.MQTT_PORT): {
            "type": "integer",
            "description": "MQTT broker port"
        },
        str(CnzDefinitions.MQTT_KEEPALIVE): {
            "type": "integer",
            "description": "MQTT keepalive interval in seconds"
        },
        str(CnzDefinitions.PASSKEY):{
            "type": "integer",
            "description": "Passkey for BLE pairing"
        },
    },
    "required": [
        str(CnzDefinitions.UUID_DEVICE_LIST),
        str(CnzDefinitions.PASSKEY),
        ],
    "additionalProperties": True,
}