from threading import Lock, Thread
import asyncio
import sys
import logging
import queue
from bleak import BleakScanner, BlueZClientArgs, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from pydbus import SystemBus
from gi.repository import GLib
from bleak.exc import BleakDBusError
from utils import AGENT_PATH, FixedPasskeyAgent

logger = logging.getLogger(__name__)

PASSKEY = 123456

service_uuid_filter = [
    '123e4567-f289-0b12-d3f6-a4f00f8d17b6',
    'f980ab40-65f5-4467-0000-7c9ebd0755ba',
    'f980ab40-65f5-4467-0000-58e6c519989a',
    'f980ab40-65f5-4467-0000-a4f00f8d17b4'
]

CHARACTERISTIC_UUIDS_PREFIXES = [
    "123e4567-f289-0b12",
    "f980ab40-65f5-4467",
    "00002902-0000-1000",  # Notification
]

# Define a small timeout for await operations to avoid blocking the event loop for too long
NO_BLOCK_TIMEOUT = 0.001

class CenzontleDevice:
    def __init__(self, address, props):
        self.address = address
        self.name = props.get('props', {}).get('Name', None)
        self.appearance = props.get('props', {}).get('Appearance', None)
        self.uuids = props.get('props', {}).get('UUIDs', None)
        self.path = props.get('props', {}).get('Path', None)
        self.client = None
        self.notify_enabled = False
        logger.info(f"Created CenzontleDevice: {props}")

    def __str__(self):
        return (f"CenzontleDevice(address={self.address}, name={self.name},"
                f" Connected={self.client.is_connected if self.client else False})")

    def __repr__(self):
        return self.__str__()

    def _compute_checksum(self, data):
        # Compute simple 2-byte checksum
        checksum = 0
        for byte in data:
            checksum += byte
        return checksum % 256

    def on_echo_command(self, kwargs):
        payload = kwargs.get('payload', None)
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
        relay_number = kwargs.get('relay_number', None)
        relay_state = kwargs.get('relay_state', None)

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
            return False

        command_api = {
            "set_relay": self.on_relay_command,
            "echo": self.on_echo_command,
        }

        command_handler = command_api.get(command, None)
        if command_handler is None:
            return False

        command_data = command_handler(kwargs)
        if command_data is None:
            return False

        await self.client.write_gatt_char('123e4567-f289-0b12-d302-a4f00f8d17b6', command_data)
        return True


class BleManager:
    def __init__(self, adapter=None):
        self.adapter = BlueZClientArgs(adapter=adapter) if adapter else None
        self.devices = {}
        self.agent_manager = None
        self.ble_lock = Lock()
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
        self._dbus_thread = Thread(target=self._dbus_loop.run, daemon=True)
        self._dbus_thread.start()
        self._command_queue = queue.Queue(maxsize=20)
        logger.info("DBus event loop running in background thread.")

    def unregister_agent(self):
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

    async def scan_devices(self, timeout=5, uuids_filter=None):
        scanner = BleakScanner(
            self.register_device, uuids_filter, bluez=self.adapter
        )

        logger.info("Starting scanner")
        if self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            self.ble_lock.acquire()
            async with scanner:
                await asyncio.sleep(timeout)
            self.ble_lock.release()

        logger.info(f"Devices: {self.devices}")

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
                            logger.warning(
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

    async def dispatch_command(self):
        if not self._command_queue.empty() and not self.ble_lock.locked():
            self.ble_lock.acquire()
            command = self._command_queue.get(False)
            # Parse the command: Device_name, command_key, args
            device_name = command.get("device_name", None)
            command_key = command.get("command_key", None)
            args = command.get("args", None)
            if None in [device_name, command_key, args]:
                logger.error(f"Invalid command: {command}")
                self.ble_lock.release()
                return
            device = manager.get(device_name, None)
            if device is None:
                logger.error(f"Device {device_name} not found")
                self.ble_lock.release()
                return
            if device.client is None or not device.client.is_connected:
                logger.error(f"Device {device_name} not connected")
                self.ble_lock.release()
                return
            await device.send_command(command_key, args)
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
                    logger.info(f"Sending command {num} to {device.name}")
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
        logger.info("Notification from %s: %s", char_uuid, value)


def simple_callback(device: BLEDevice, advertisement_data: AdvertisementData):
    logger.info(
        "addr: %s, name: %s, %r", device.address, device.name, advertisement_data
    )


async def monitored_task(coro_func, *args, name, loop_delay=5, restart_delay=2, **kwargs):
    """Run a task with monitoring and restart"""
    restart_count = 0

    while True:
        try:
            if restart_count == 0:
                logging.info(f"Task {name} started")
            restart_count += 1

            await coro_func(*args, **kwargs)

            await asyncio.sleep(loop_delay)

        except asyncio.CancelledError:
            logging.info(f"Task {name} cancelled after {restart_count} runs")
            break

        except Exception as e:
            logging.error(f"Task {name} crashed after {restart_count} runs: {e}")
            logging.info(f"Task Restarting {name} in {restart_delay}s...")
            await asyncio.sleep(restart_delay)


async def main(manager):
    async with asyncio.TaskGroup() as tg:

        # Create monitored tasks
        task1 = tg.create_task(monitored_task(manager.scan_devices,
                                              5, service_uuid_filter,
                                              name="DeviceScanner",
                                              loop_delay=15,
                                              restart_delay=5,
                                              ))
        task2 = tg.create_task(monitored_task(manager.connect_devices, name="ClientTask", restart_delay=5))
        task3 = tg.create_task(monitored_task(manager.enable_notifications, name="EnableNotificationTask", restart_delay=5))
        #task4 = tg.create_task(monitored_task(manager.dispatch_command, name="CommandTask", restart_delay=5))
        task5 = tg.create_task(monitored_task(manager.test_connect_devices, name="TestConnectTask", restart_delay=5))

        await asyncio.Event().wait()

if __name__ == "__main__":
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)-15s %(name)-8s %(levelname)s: %(message)s",
    )

    #ñmanager = BleManager(adapter="hci0")
    manager = BleManager(adapter="hci1")

    try:
        asyncio.run(main(manager))
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        manager.unregister_agent()
    except BleakDBusError as e:
        if (e.dbus_error == "org.bluez.Error.ConnectionAttemptFailed" and
                "Page Timeout" in e.dbus_error_details):
            # Blocking error, no known solution, quit safely in the future
            logging.error(
                f"Connection attempt failed ({e.dbus_error} - {e.dbus_error_details}), quitting safely.")
            manager.unregister_agent()
            sys.exit(0)
    except Exception:
        manager.unregister_agent()
        raise
