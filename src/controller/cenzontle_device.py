from data import BleServiceUri
from utils import CnzDefinitions


class CenzontleDevice:
    def __init__(self, address, props):
        self.address = address
        self.name = props.get('props', {}).get('Name', None)
        self.appearance = props.get('props', {}).get('Appearance', None)
        self.uuids = props.get('props', {}).get('UUIDs', None)
        self.path = props.get('props', {}).get('Path', None)
        self.client = None
        self.notify_enabled = False
        self._notify_uri = f"{BleServiceUri.NOTIFY[:-12]}{self.address.replace(':', '')}"
        self._command_api_uri = f"{BleServiceUri.COMMAND_API[:-12]}{self.address.replace(':', '')}"

    def __str__(self):
        return (f"CenzontleDevice(address={self.address}, name={self.name},"
                f" Connected={self.client.is_connected if self.client else False})")

    def __repr__(self):
        return self.__str__()

    @property
    def notify_uri(self):
        return self._notify_uri

    @property
    def command_api_uri(self):
        return self._command_api_uri

    def _compute_checksum(self, data):
        # Compute simple 2-byte checksum
        checksum = 0
        for byte in data:
            checksum += byte
        return checksum % 256

    def on_echo_command(self, kwargs):
        payload = kwargs.get(str(CnzDefinitions.PAYLOAD), None)
        if payload is None:
            return None
        # Check that payload is a bytearray
        if not isinstance(payload, bytearray):
            return None
        command = bytearray([0x0A]) + payload
        checksum = self._compute_checksum(command)
        command.extend([(checksum >> 8) & 0xFF, checksum & 0xFF])
        return command

    def on_relay_command(self, kwargs):
        # validate kwargs
        relay_number = kwargs.get(str(CnzDefinitions.RELAY_NUMBER), None)
        relay_state = kwargs.get(str(CnzDefinitions.RELAY_STATE), None)

        if None in (relay_number, relay_state):
            return None

        if not isinstance(relay_number, int) or not isinstance(relay_state, bool):
            return None

        command = bytearray([0x09, relay_number & 0xFF, 0x01 if relay_state else 0x00])
        checksum = self._compute_checksum(command)
        command.extend([(checksum >> 8) & 0xFF, checksum & 0xFF])

        return command

    async def send_command(self, command, kwargs=None):
        if self.client is None:
            pass
            # log instead

        command_api = {
            str(CnzDefinitions.SET_RELAY): self.on_relay_command,
            str(CnzDefinitions.ECHO): self.on_echo_command,
        }

        command_handler = command_api.get(command, None)
        if command_handler is None:
            pass
            # log instead

        command_data = command_handler(kwargs)
        if command_data is None:
            pass
            # log instead

        await self.client.write_gatt_char(self.command_api_uri, command_data)
