import threading
import asyncio
import logging
from bleak import BleakScanner, BlueZClientArgs, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from pydbus import SystemBus
from gi.repository import GLib
from bleak.exc import BleakDBusError
from utils import FixedPasskeyAgent

logger = logging.getLogger(__name__)

AGENT_PATH = "/org/bluez/agent"
PASSKEY = 123456

CHARACTERISTIC_UUIDS_PREFIXES = [
    "123e4567-f289-0b12",
    "f980ab40-65f5-4467",
    "00002902-0000-1000", # Notification
]


class CenzontleDevice:
    def __init__(self, address, props):
        self.address = address
        self.name = props.get('props', {}).get('Name', None)
        self.appearance = props.get('props', {}).get('Appearance', None)
        self.uuids = props.get('props', {}).get('UUIDs', None)
        self.path = props.get('props', {}).get('Path', None)
        self.paired = props.get('props', {}).get('Paired', None)
        self.client = None

    def __str__(self):
        return f"CenzontleDevice(address={self.address}, name={self.name})"

    def __repr__(self):
        return self.__str__()


class BleManager:
    def __init__(self, adapter=None):
        self.adapter = BlueZClientArgs(adapter=adapter) if adapter else None
        self.devices = {}
        self.agent_manager = None
        self._dbus_loop = None
        self._dbus_thread = None
        self._register_agent()
        # busctl introspect org.bluez /org/bluez/hci0/ # busctl introspect org.bluez /org/bluez/hci0/dev_D8_3A_DD_37_52_03

    def _register_agent(self):
        self._dbus_loop = GLib.MainLoop()
        bus = SystemBus()
        agent = FixedPasskeyAgent(PASSKEY)
        bus.register_object(AGENT_PATH, agent, None)
        self.agent_manager = bus.get("org.bluez", "/org/bluez")
        self.agent_manager.RegisterAgent(AGENT_PATH, "KeyboardOnly")
        self.agent_manager.RequestDefaultAgent(AGENT_PATH)
        logger.info("DBus agent registered.")
        self._dbus_thread = threading.Thread(target=self._dbus_loop.run, daemon=True)
        self._dbus_thread.start()
        logger.info("DBus event loop running in background thread.")

    def _unregister_agent(self):
        if self.agent_manager:
            try:
                self.agent_manager.UnregisterAgent(AGENT_PATH)
                logger.info("DBus agent unregistered.")
            except Exception as e:
                logger.error(f"Error unregistering agent: {e}")
        if self._dbus_loop and self._dbus_loop.is_running():
            self._dbus_loop.quit()
            logger.info("DBus event loop stopped.")

    def register_device(self, device: BLEDevice, advertisement_data: AdvertisementData):
        logger.info(
            "addr: %s, details: %s, %r", device.address, device.details, advertisement_data
        )
        address = device.address
        if address and address not in self.devices:
            device_obj = CenzontleDevice(address, device.details)
            self.devices[device_obj.name] = device_obj

    async def scan_devices(self, timeout=5, uuids_filter=None, clean_devices=False):
        scanner = BleakScanner(
            self.register_device, uuids_filter, bluez=self.adapter
        )

        logger.info("Starting scanner")
        async with scanner:
            await asyncio.sleep(timeout)

        logger.info(f"Devices: {self.devices}")

    async def connect_devices(self):
        for device in self.devices.values():
            if device.client is None:
                try:
                    device.client = BleakClient(device.address, pair=True, timeout=10)
                    await device.client.__aenter__()
                    await asyncio.sleep(2)
                except BleakDBusError as e:
                    if (e.dbus_error == "org.bluez.Error.ConnectionAttemptFailed" and
                            "Page Timeout" in e.dbus_error_details):
                        # End of life of this run, we will retry later
                        logger.warning(f"Connection attempt failed for {device.name} ({e.dbus_error} - {e.dbus_error_details}), will retry later.")
                        continue
                    raise


                # Log the client.services:
            logger.info("Services: %s", device.client.services.services)
            logger.info("Chars: %s", device.client.services.characteristics)
            for chars in device.client.services.characteristics.values():
                logger.info("char val: %s", chars.uuid)
            # Enable notifications, not working right now
            await device.client.start_notify('f980ab40-65f5-4467-0002-58e6c519989a', self.notify_callback)
            # Send command
            await device.client.write_gatt_char('f980ab40-65f5-4467-0002-58e6c519989a',
                                         bytearray([0x0A, 0x30, 0x31, 0x32, 0x030, 0x00, 0xCD]))
            await asyncio.sleep(2)
            await device.client.write_gatt_char('f980ab40-65f5-4467-0002-58e6c519989a',
                                         bytearray([0x09, 0x01, 0x01, 0x00, 0x0B]))
            await asyncio.sleep(2)
            await device.client.write_gatt_char('f980ab40-65f5-4467-0002-58e6c519989a',
                                         bytearray([0x09, 0x01, 0x00, 0x00, 0x0A]))
            await asyncio.sleep(2)

    async def disconnect_devices(self):
        for device in self.devices.values():
            if device.client is not None:
                await device.client.__aexit__(None, None, None)

    def notify_callback(self, char_uuid, value):
        logger.info("Notification from %s: %s", char_uuid, value)

"""if __name__ == '__main__':
    #manager = BleManager(["00:1A:7D:DA:71:15"])
    # If no prefered devices are given, the first one will be used
    manager = BleManager()

    # service_uuid_filter = ['123e4567-f289-0b12-d3f6-a4f00f8d17b6', 'f980ab40-65f5-4467-0000-a4f00f8d17b4']
    service_uuid_filter = [
        '123e4567-f289-0b12-d3f6-a4f00f8d17b6',
        'f980ab40-65f5-4467-0000-7c9ebd0755ba',
        'f980ab40-65f5-4467-0000-a4f00f8d17b4'
    ]

    manager.scan_devices(0,
                         callback=None,
                         timeout=5,
                         uuids_filter=service_uuid_filter,
                         clean_devices=False
    # clean_devices=True
    )
    print(manager.devices)
    manager.connect_devices()
    print(f"Connected !")
    # Discover characteristics
    manager.read_characteristics()

    # Demo: send command

    # subscribe to characteristic
    manager.send_command("CENZ-0F8D17B6", '00002902-0000-1000-8000-00805f9b34fb', [0x00, 0x01])"""

"""manager.send_command("CENZ-0F8D17B6", '123e4567-f289-0b12-d302-a4f00f8d17b6', [0x09, 0x01, 0x01, 0x00, 0x0B])
    time.sleep(2)
    manager.send_command("CENZ-0F8D17B6", '123e4567-f289-0b12-d302-a4f00f8d17b6', [0x09, 0x01, 0x00, 0x00, 0x0A])
    time.sleep(2)"""
    # Send command once connected

service_uuid_filter = [
        '123e4567-f289-0b12-d3f6-a4f00f8d17b6',
        'f980ab40-65f5-4467-0000-7c9ebd0755ba',
        'f980ab40-65f5-4467-0000-58e6c519989a',
        'f980ab40-65f5-4467-0000-a4f00f8d17b4'
    ]

def simple_callback(device: BLEDevice, advertisement_data: AdvertisementData):
    logger.info(
        "addr: %s, name: %s, %r", device.address, device.name, advertisement_data
    )

async def main():
    manager = BleManager(adapter="hci0")
    await manager.scan_devices(timeout=5, uuids_filter=service_uuid_filter)
    await manager.connect_devices()
    await manager.disconnect_devices()
    try:
        manager.UnregisterAgent(AGENT_PATH)
    except Exception:
        pass
    # Pair device
    """for device in manager.devices.values():
        async with BleakClient(device.address, pair=True, timeout=30) as client:
            try:
                await asyncio.sleep(2)
                logger.info("Connected: %s", client.is_connected)
            finally:
                try:
                    manager.UnregisterAgent(AGENT_PATH)
                except Exception:
                    pass
                # Log the client.services:
                logger.info("Services: %s", client.services.services)
                logger.info("Chars: %s", client.services.characteristics)
                for chars in client.services.characteristics.values():
                    logger.info("char val: %s", chars.uuid)
                # Enable notifications, not working right now
                await client.start_notify('123e4567-f289-0b12-d302-a4f00f8d17b6', manager.notify_callback)
                # Send command
                await client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6',
                                             bytearray([0x0A, 0x30, 0x31, 0x32, 0x030, 0x00, 0xCD]))
                await asyncio.sleep(2)
                await client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6', bytearray([0x09, 0x01, 0x01, 0x00, 0x0B]))
                await asyncio.sleep(2)
                await client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6',
                                             bytearray([0x09, 0x01, 0x00, 0x00, 0x0A]))
                await asyncio.sleep(2)"""


if __name__ == "__main__":
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)-15s %(name)-8s %(levelname)s: %(message)s",
    )
    #manager = BleManager(adapter="hci1")

    asyncio.run(main())
    #asyncio.run(manager.scan_devices(timeout=5, uuids_filter=service_uuid_filter))