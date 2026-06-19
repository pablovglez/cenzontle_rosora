from utils import CnzDefinitions


config_schema = {
    "type": "object",
    "properties": {
        str(CnzDefinitions.UUID_DEVICE_LIST): {
            "type": "array",
            "items": {
                "type": "string",
                "description": "UUID of the device"
            },
        }
    },
    "required": [str(CnzDefinitions.UUID_DEVICE_LIST)],
    "additionalProperties": True,
}