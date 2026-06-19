import enum

# General Definitions

class CnzDefinitions(enum.StrEnum):
    STATUS = "status"
    ONLINE = "online"
    OFFLINE = "offline"

    UUID_DEVICE_LIST = "uuid_device_list"

    def __str__(self):
        return self.value

