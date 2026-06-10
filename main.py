from gc import callbacks
from xml.dom.pulldom import CHARACTERS

from pydbus import SystemBus
from gi.repository import GLib
import threading
import time
import asyncio
from bleak import BleakScanner, BlueZScannerArgs, BlueZClientArgs, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
import argparse
import logging

logger = logging.getLogger(__name__)

AGENT_PATH = "/org/bluez/agent"
PASSKEY = 123456

AGENT_XML = """  
<node>
  <interface name="org.bluez.Agent1">
    <method name="RequestPasskey">
      <arg direction="in" type="o"/>
      <arg direction="out" type="u"/>
    </method>
    <method name="Cancel"/>
    <method name="Release"/>
  </interface>
</node>
"""

CHARACTERISTIC_UUIDS_PREFIXES = [
    "123e4567-f289-0b12",
    "f980ab40-65f5-4467",
    "00002902-0000-1000", # Notification
]

class FixedPasskeyAgent:
    dbus = AGENT_XML

    def __init__(self, passkey: int):
        self._passkey = passkey

    def RequestPasskey(self, device):
        logger.info("RequestPasskey for %s", device)
        return self._passkey

    def Cancel(self): pass

    def Release(self): pass


class CenzontleDevice:
    def __init__(self, address, props):
        self.address = address
        self.name = props.get('props', {}).get('Name', None)
        self.appearance = props.get('props', {}).get('Appearance', None)
        self.paired = props.get('props', {}).get('Paired', None)
        self.bonded = props.get('props', {}).get('Bonded', None)
        self.connected = props.get('props', {}).get('Connected', None)
        self.uuids = props.get('props', {}).get('UUIDs', None)
        self.path = props.get('props', {}).get('Path', None)
        self.client = None

    def __str__(self):
        return f"CenzontleDevice(address={self.address}, name={self.name})"

    def __repr__(self):
        return self.__str__()


class Adapter:
    def __init__(self, name, address, path, powered, props=None):
        self.name = name
        self.address = address
        self.path = path
        self.powered = powered
        self.props = props or {}

    def __repr__(self):
        return f"Adapter(name={self.name}, address={self.address}, path={self.path}, powered={self.powered})"


class BleManager:
    def __init__(self, adapter=None):
        # Need to unblock bluetooth via rfkill
        # rfkill list bluetooth
        # sudo rfkill unblock bluetooth
        #self.bus = SystemBus()
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

    def _fetch_adapters(self, prefered_devices):
        adapters = {}
        bluez = self.bus.get("org.bluez", "/")
        managed = bluez.GetManagedObjects()
        for path, ifaces in managed.items():
            adapter_props = ifaces.get("org.bluez.Adapter1")
            if adapter_props:
                index = len(adapters)
                address = adapter_props.get("Address")
                if not prefered_devices or address in prefered_devices:
                    adapters[index] = Adapter(path.replace("/org/bluez/", ""),
                                              address,
                                              path, adapter_props.get("Powered")
                                              )
                    # Check if adapter is powered
                    adapter_iface = self.bus.get("org.bluez", adapters[index].path)
                    if not adapters[index].powered:
                        adapter_iface.Set("org.bluez.Adapter1", "Powered", GLib.Variant("b", True))
                    # Set adapter to connectable if needed
                    adapter_iface.Set("org.bluez.Adapter1", "Connectable", GLib.Variant("b", True))

                    return adapters

        return None

    def _remove_devices(self, adapter):
        bluez = self.bus.get("org.bluez", "/")
        managed = bluez.GetManagedObjects()
        adapter_iface = self.bus.get("org.bluez", adapter.path)

        for path, ifaces in managed.items():
            if "org.bluez.Device1" in ifaces and path.startswith(adapter.path):
                try:
                    adapter_iface.RemoveDevice(path)
                except Exception as e:
                    print(f"Could not remove {path}: {e}")

    def _get_device_path(self, adapter, address):
        mac = address.replace(":", "_")
        return f"{adapter.path}/dev_{mac}"

    def _uuid_to_mac(self, uuid:str):
        return ":".join([uuid[-12:].upper()[i:i+2] for i in range(0, 12, 2)])

    """def connect(self, adapter_key, address):
        adapter = self.adapters[adapter_key]
        path = self._get_device_path(adapter, address)
        device_iface = self.bus.get("org.bluez", path)

        device_iface.Connect()"""

    def pair(self, adapter_key, address, cli_mode=False):
        adapter = self.adapters[adapter_key]
        path = self._get_device_path(adapter, address)
        device_iface = self.bus.get("org.bluez", path)
        if cli_mode:
            device_iface.Pair()
        else:
            loop = GLib.MainLoop()
            agent = FixedPasskeyAgent(123456)
            self.bus.register_object(AGENT_PATH, agent, None)
            agent_manager = self.bus.get("org.bluez", "/org/bluez")
            agent_manager.RegisterAgent(AGENT_PATH, "KeyboardOnly")
            agent_manager.RequestDefaultAgent(AGENT_PATH)

            loop_thread = threading.Thread(target=loop.run, daemon=True)
            loop_thread.start()
            try:
                device_iface.Pair()
                print(f"Paired with {address}")
            except Exception as e:
                print(f"Pair failed: {e}")
            finally:
                loop.quit()
            try:
                agent_manager.UnregisterAgent(AGENT_PATH)
            except Exception:
                pass

    """def scan_devices(self, adapter_key, callback=None, timeout=5, uuids_filter=None, clean_devices=False):
        if not self.adapters or adapter_key not in self.adapters:
            return False

        adapter = self.adapters[adapter_key]
        adapter_iface = self.bus.get("org.bluez", adapter.path)

        if clean_devices:
            self._remove_devices(adapter)
        if uuids_filter:
            adapter_iface.SetDiscoveryFilter({"UUIDs": GLib.Variant("as", uuids_filter)})

        adapter_iface.StartDiscovery()
        time.sleep(timeout)
        adapter_iface.StopDiscovery()
        bluez = self.bus.get("org.bluez", "/")
        managed = bluez.GetManagedObjects()
        for path, ifaces in managed.items():
            device_props = ifaces.get("org.bluez.Device1")
            if device_props and path.startswith(adapter.path):
                if uuids_filter:
                    device_uuids = device_props.get("UUIDs", [])
                    if not any(uuid in device_uuids for uuid in uuids_filter):
                        continue

                if callback:
                    callback(device_props)
                address = device_props.get("Address")
                if address and address not in self.devices:
                    device_obj = CenzontleDevice(address, device_props)
                    self.devices[device_obj.name] = device_obj

        return True"""

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
            #self.register_device, uuids_filter, bluez={"adapter": self.adapter}
            self.register_device, uuids_filter, bluez=self.adapter
        )

        logger.info("Starting scanner")
        async with scanner:
            await asyncio.sleep(timeout)

        logger.info(f"Devices: {self.devices}")

    def notify_callback(self, char_uuid, value):
        logger.info("Notification from %s: %s", char_uuid, value)

    def connect_devices(self):
        for key, device in self.devices.items():
            if not device.paired:
                self.pair(0, device.address, False)
                print(f"Paired device")
            elif not device.connected:
                self.connect(0, device.address)
                print(f"Connected device")

    def read_characteristics(self):
        bluez = self.bus.get("org.bluez", "/")
        managed = bluez.GetManagedObjects()
        for path, ifaces in managed.items():
            gatt_chars = ifaces.get("org.bluez.GattCharacteristic1")
            print(gatt_chars)
            char_uuid = gatt_chars.get("UUID", None) if gatt_chars else None
            if char_uuid is not None and any(char_uuid.startswith(prefix) for prefix in CHARACTERISTIC_UUIDS_PREFIXES):
                # Find in device list
                device_name = f"CENZ-{char_uuid[-8:].upper()}"
                if device_name in self.devices.keys():
                    self.devices[device_name].gatt_map[char_uuid] = {
                        "Handle": gatt_chars.get("Handle", None),
                        "path": path
                    }
            print("****")
            print(ifaces.get("org.bluez.GattDescriptor1"))
        for device in self.devices.values():
            print(device.gatt_map)

    def send_command(self, device_name, char_uuid, command):
        if device_name in self.devices.keys():
            device = self.devices[device_name]
            if char_uuid in device.gatt_map.keys():
                device_bus = self.bus.get("org.bluez", device.gatt_map[char_uuid].get('path'))
                device_bus.WriteValue(command, {}) if device_bus else None

    #def enable_notifications(self, device_name, char_uuid):

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

    # Pair device
    for device in manager.devices.values():
        async with BleakClient(device.address, pair=True) as client:
            try:
                """if not device.paired:
                    await client.pair()"""
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
                await asyncio.sleep(2)


if __name__ == "__main__":
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)-15s %(name)-8s %(levelname)s: %(message)s",
    )
    #manager = BleManager(adapter="hci1")

    asyncio.run(main())
    #asyncio.run(manager.scan_devices(timeout=5, uuids_filter=service_uuid_filter))