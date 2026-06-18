from threading import Lock, Thread
import asyncio
import logging
import queue
from bleak import BleakScanner, BlueZClientArgs, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from pydbus import SystemBus
from gi.repository import GLib
from bleak.exc import BleakDBusError
from .cenzontle_device import CenzontleDevice
from utils import AGENT_PATH, FixedPasskeyAgent

PASSKEY = 123456
# Define a small timeout for await operations to avoid blocking the event loop for too long
NO_BLOCK_TIMEOUT = 0.001


#Change name to BleController
class BleManager:
    def __init__(self, adapter=None, logger_mgr=None):
        self.adapter = BlueZClientArgs(adapter=adapter) if adapter else None
        self._logger_mgr = logger_mgr
        self.logger = self._logger_mgr.add_child_logger(self.__class__.__name__)
        self.devices = {}
        self.agent_manager = None
        self.ble_lock = Lock()
        self._dbus_loop = None
        self._dbus_thread = None
        self._register_agent()

    def _register_agent(self):
        self._dbus_loop = GLib.MainLoop()
        bus = SystemBus()
        agent = FixedPasskeyAgent(PASSKEY)
        bus.register_object(AGENT_PATH, agent, None)
        self.agent_manager = bus.get("org.bluez", "/org/bluez")
        self.agent_manager.RegisterAgent(AGENT_PATH, "KeyboardOnly")
        self.agent_manager.RequestDefaultAgent(AGENT_PATH)
        self.logger.info("DBus agent registered.")
        self._dbus_thread = Thread(target=self._dbus_loop.run, daemon=True)
        self._dbus_thread.start()
        self._command_queue = queue.Queue(maxsize=20)
        self.logger.info("DBus event loop running in background thread.")

    def unregister_agent(self):
        if self.agent_manager:
            try:
                self.agent_manager.UnregisterAgent(AGENT_PATH)
                self.logger.info("DBus agent unregistered.")
            except Exception as e:
                self.logger.error(f"Error unregistering agent: {e}")
        if self._dbus_loop and self._dbus_loop.is_running():
            self._dbus_loop.quit()
            self.logger.info("DBus event loop stopped.")

    def register_device(self, device: BLEDevice, advertisement_data: AdvertisementData):
        self.logger.info(
            "addr: %s, details: %s, %r", device.address, device.details, advertisement_data
        )
        address = device.address
        if address and address not in self.devices:
            device_obj = CenzontleDevice(address, device.details)
            self.devices[device_obj.name] = device_obj

    async def scan_devices(self, timeout=5, uuids_filter=None):
        scanner = BleakScanner(
            self.register_device, uuids_filter, bluez=self.adapter
        )

        self.logger.info("Starting scanner")
        if self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            self.ble_lock.acquire()
            async with scanner:
                await asyncio.sleep(timeout)
            self.ble_lock.release()

        self.logger.info(f"Devices: {self.devices}")

    async def connect_devices(self):
        if not self.devices or self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            self.ble_lock.acquire()
            for device in self.devices.values():
                if device.client is None or not device.client.is_connected:
                    try:
                        device.client = BleakClient(device.address, pair=True, timeout=10)
                        await device.client.__aenter__()
                        await asyncio.sleep(NO_BLOCK_TIMEOUT)
                    except BleakDBusError as e:
                        if (e.dbus_error == "org.bluez.Error.ConnectionAttemptFailed" and
                                "Page Timeout" in e.dbus_error_details):
                            # End of life of this run, we will retry later
                            self.ble_lock.release()
                            self.logger.warning(
                                f"Connection attempt failed for {device.name} ({e.dbus_error} - {e.dbus_error_details}), will retry later.")
                            await asyncio.sleep(NO_BLOCK_TIMEOUT)
                            continue
                        raise
                    except Exception:
                        self.ble_lock.release()
                        raise

            self.ble_lock.release()

    async def enable_notifications(self):
        if not self.devices or self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            self.ble_lock.acquire()
            for device in self.devices.values():
                if device.client is None or not device.client.is_connected:
                    continue
                if device.notify_enabled:
                    continue
                try:
                    await device.client.start_notify('123e4567-f289-0b12-d302-a4f00f8d17b6', self.notify_callback)
                    device.notify_enabled = True
                    await asyncio.sleep(NO_BLOCK_TIMEOUT)
                except Exception:
                    self.ble_lock.release()
                    raise
            self.ble_lock.release()

    def queue_command(self, device_name, command_key, args):
        self._command_queue.put({"device_name": device_name, "command_key": command_key, "args": args})

    async def dispatch_command(self):
        if not self._command_queue.empty() and not self.ble_lock.locked():
            self.ble_lock.acquire()
            command = self._command_queue.get(False)
            self.logger.info(f"Dispatching command {command}")
            # Parse the command: Device_name, command_key, args
            device_name = command.get("device_name", None)
            command_key = command.get("command_key", None)
            args = command.get("args", None)
            if None in [device_name, command_key, args]:
                self.logger.error(f"Invalid command: {command}")
                self.ble_lock.release()
                return
            device = self.devices.get(device_name, None)
            if device is None:
                self.logger.error(f"Device {device_name} not found")
                self.ble_lock.release()
                return
            if device.client is None or not device.client.is_connected:
                self.logger.error(f"Device {device_name} not connected")
                self.ble_lock.release()
                return
            await device.send_command(command_key, args)
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            await asyncio.sleep(NO_BLOCK_TIMEOUT)


    async def test_connect_devices(self):
        if not self.devices or self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            self.ble_lock.acquire()
            for device in self.devices.values():
                if device.client is None or not device.client.is_connected:
                    continue
                # Log the client.services:
                """logger.info("Services: %s", device.client.services.services)
                logger.info("Chars: %s", device.client.services.characteristics)
                for chars in device.client.services.characteristics.values():
                    logger.info("char val: %s", chars.uuid)
                # Enable notifications, not working right now
                try:
                    await device.client.start_notify('123e4567-f289-0b12-d302-a4f00f8d17b6', self.notify_callback)
                except Exception as e:
                    logger.error(f"Error starting notifications: {e}")"""
                # Send command
                await device.client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6',
                                                    bytearray([0x0A, 0x30, 0x31, 0x32, 0x030, 0x00, 0xCD]))
                await asyncio.sleep(2)
                await device.client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6',
                                                    bytearray([0x09, 0x01, 0x01, 0x00, 0x0B]))
                await asyncio.sleep(2)
                await device.client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6',
                                                    bytearray([0x09, 0x01, 0x00, 0x00, 0x0A]))
                await asyncio.sleep(2)
                await device.send_command("set_relay", {"relay_number": 1, "relay_state": True})
                await asyncio.sleep(2)
                await device.send_command("set_relay", {"relay_number": 1, "relay_state": False})
                for i in range(1, 24):
                    num = i % 4 + 1
                    self.logger.info(f"Sending command {num} to {device.name}")
                    await asyncio.sleep(0.25)
                    await device.send_command("set_relay", {"relay_number": num, "relay_state": True})
                    await asyncio.sleep(0.25)
                    await device.send_command("set_relay", {"relay_number": num, "relay_state": False})
            self.ble_lock.release()

    async def disconnect_devices(self):
        for device in self.devices.values():
            if device.client is not None:
                await device.client.__aexit__(None, None, None)

    def notify_callback(self, char_uuid, value):
        self.logger.info("Notification from %s: %s", char_uuid, value)