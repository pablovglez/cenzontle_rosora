import enum

# General Definitions

class MQTT_API(enum.StrEnum):
    STATUS = "status"
    ONLINE = "online"
    OFFLINE = "offline"

    def __str__(self):
        return self.value

