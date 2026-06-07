from gc import callbacks
from xml.dom.pulldom import CHARACTERS

from pydbus import SystemBus
from gi.repository import GLib
import threading
import time
import asyncio

AGENT_PATH = "/org/bluez/agent"

AGENT_XML = """  
<node>
  <interface name="org.bluez.Agent1">
    <method name="RequestPasskey">
      <arg type="o" name="device" direction="in"/>
      <arg type="u" name="passkey" direction="out"/>
    </method>
    <method name="Cancel"/>
    <method name="Release"/>
  </interface>
</node>
"""

CHARACTERISTIC_UUIDS_PREFIXES = [
    "123e4567-f289-0b12",
    "f980ab40-65f5-4467",
]

class FixedPasskeyAgent:
    dbus = AGENT_XML

    def __init__(self, passkey: int):
        self._passkey = passkey

    def RequestPasskey(self, device):
        return self._passkey

    def Cancel(self): pass

    def Release(self): pass


class CenzontleDevice:
    def __init__(self, address, props):
        self.address = address
        self.name = props.get('Name', None)
        self.appearance = props.get('Appearance', None)
        self.paired = props.get('Paired', None)
        self.bonded = props.get('Bonded', None)
        self.connected = props.get('Connected', None)
        self.uuids = props.get('UUIDs', None)
        self.path = f"{props.get('Adapter', None)}/dev_{self.address.replace(':', '_')}"
        self.gatt_map = {}

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
    def __init__(self, prefered_devices=[]):
        # Need to unblock bluetooth via rfkill
        # rfkill list bluetooth
        # sudo rfkill unblock bluetooth
        self.bus = SystemBus()
        self.adapters = self._fetch_adapters(prefered_devices)
        self.devices = {}
        # busctl introspect org.bluez /org/bluez/hci0/ # busctl introspect org.bluez /org/bluez/hci0/dev_D8_3A_DD_37_52_03

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

    def connect(self, adapter_key, address):
        adapter = self.adapters[adapter_key]
        path = self._get_device_path(adapter, address)
        device_iface = self.bus.get("org.bluez", path)

        device_iface.Connect()

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

    def scan_devices(self, adapter_key, callback=None, timeout=5, uuids_filter=None, clean_devices=False):
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
        found_gatt_chars = {}
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

        return True

    def connect_devices(self):
        for key, device in self.devices.items():
            if not device.paired:
                self.pair(0, device.address, False)
                print(f"Paired device")
            elif not device.connected:
                manager.connect(0, device.address)
                print(f"Connected device")

    def read_characteristics(self):
        bluez = self.bus.get("org.bluez", "/")
        managed = bluez.GetManagedObjects()
        for path, ifaces in managed.items():
            gatt_chars = ifaces.get("org.bluez.GattCharacteristic1")
            char_uuid = gatt_chars.get("UUID", None) if gatt_chars else None
            if char_uuid is not None and char_uuid[:18] in CHARACTERISTIC_UUIDS_PREFIXES:
                # Find in device list
                device_name = f"CENZ-{char_uuid[-8:].upper()}"
                if device_name in self.devices.keys():
                    self.devices[device_name].gatt_map[char_uuid] = {
                        "Handle": gatt_chars.get("Handle", None),
                        "path": path
                    }
        for device in self.devices.values():
            print(device.gatt_map)

    def send_command(self, device_name, command):
        if device_name in self.devices.keys():
            path = f'{self.devices[device_name].path}/service0028/char0029'
            device_bus = self.bus.get("org.bluez", path)
            #device_bus = self.bus.get("org.bluez", self._get_device_path(self.adapters[0], self.devices[device_name].address))
            print(f"Sending command {command} to {device_name} at path {self.devices[device_name].path}")
            #print(device_bus.Introspect())
            #device_bus.WriteValue("org.bluez.GattCharacteristic1.WriteValue", "aya{sv}", command, {})
            device_bus.WriteValue(command, {})

if __name__ == '__main__':
    manager = BleManager(["00:1A:7D:DA:71:15"])

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

    # Run async manager.connect_devices()
    #asyncio.run(manager.connect_devices())
    #asyncio.run(manager.send_command("CENZ-0F8D17B6", [0x09, 0x01, 0x01, 0x00, 0x0B]))
    print(manager.devices)
    manager.connect_devices()
    print(f"Connected !")
    # Discover characteristics
    manager.read_characteristics()

    # Demo: send command

    manager.send_command("CENZ-0F8D17B6", [0x09, 0x01, 0x01, 0x00, 0x0B])
    time.sleep(5)
    manager.send_command("CENZ-0F8D17B6", [0x09, 0x01, 0x00, 0x00, 0x0A])
    # Send command once connected