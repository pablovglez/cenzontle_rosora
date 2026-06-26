import enum


BleNotifyUriList = [
    'f980ab40-65f5-4467-0002-000000000000',
    '123e4567-f289-0b12-d302-000000000000'
]

class BleServiceUri(str, enum.Enum):
    NOTIFY = 'f980ab40-65f5-4467-0004-000000000000',
    #NOTIFY = '123e4567-f289-0b12-d304-000000000000',
    COMMAND_API = 'f980ab40-65f5-4467-0002-000000000000',
    #COMMAND_API = '123e4567-f289-0b12-d302-000000000000',

    def __str__(self):
        return self.value[0]