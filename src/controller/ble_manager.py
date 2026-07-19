from threading import Lock, Thread
import asyncio
import queue
import json
import struct
from bleak import BleakScanner, BlueZClientArgs, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from pydbus import SystemBus
from gi.repository import GLib
from bleak.exc import BleakDBusError, BleakDeviceNotFoundError
from .cenzontle_device import CenzontleDevice
from utils import AGENT_PATH, FixedPasskeyAgent, CnzDefinitions, NotificationsEnum

# Define a small timeout for await operations to avoid blocking the event loop for too long
NO_BLOCK_TIMEOUT = 0.001


#Change name to BleController
class BleManager:
    def __init__(self, adapter=None, logger_mgr=None, mqtt_client=None, config_data={}):
        if None in [logger_mgr, mqtt_client]:
            raise ValueError("Logger manager and MQTT manager must be provided")
        self.adapter = BlueZClientArgs(adapter=adapter) if adapter else None
        self._logger_mgr = logger_mgr
        self.logger = self._logger_mgr.add_child_logger(self.__class__.__name__)
        self.mqtt = mqtt_client
        self.devices = {}
        self.agent_manager = None
        self.ble_lock = Lock()
        self._dbus_loop = None
        self._dbus_thread = None
        self._passkey = config_data.get(str(CnzDefinitions.PASSKEY), 000000)
        self._register_agent()

    def _register_agent(self):
        self._dbus_loop = GLib.MainLoop()
        bus = SystemBus()
        agent = FixedPasskeyAgent(self._passkey)
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

    def stop_manager(self):
        for device in self.devices.values():
            self.mqtt.publish(
                str(CnzDefinitions.DEVICE_TOPIC) + "/" + device.name.upper() + "/" + str(CnzDefinitions.STATUS),
                str(CnzDefinitions.DISCONNECTED),
                retain=False)

    def register_device(self, device: BLEDevice, advertisement_data: AdvertisementData):
        self.logger.debug(
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
        previous_list = self.devices.copy()

        if not self.ble_lock.locked():
            self.logger.debug("Starting scanner")
            self.ble_lock.acquire()
            async with scanner:
                await asyncio.sleep(timeout)
            self.ble_lock.release()
        if previous_list.keys() != self.devices.keys():
            self.logger.info(f"Devices: {self.devices}")

    async def connect_devices(self):
        devices_to_pop = []
        if not self.devices or self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            for device in self.devices.values():
                if device.client is None or not device.client.is_connected:
                    try:
                        self.ble_lock.acquire()
                        device.client = BleakClient(device.address, pair=True, timeout=10)
                        await device.client.__aenter__()
                        self.ble_lock.release()
                        self.mqtt.publish(
                            str(CnzDefinitions.DEVICE_TOPIC) + "/" + device.name.upper() + "/" + str(CnzDefinitions.STATUS),
                            str(CnzDefinitions.CONNECTED),
                            retain=True)
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
                    except BleakDeviceNotFoundError:
                        self.logger.debug(f'Device {device.name} is not visible anymore, removing from list')
                        devices_to_pop.append(device.name)
                        self.mqtt.publish(
                            str(CnzDefinitions.DEVICE_TOPIC) + "/" + device.name.upper() + "/" + str(CnzDefinitions.STATUS),
                            str(CnzDefinitions.DISCONNECTED),
                            retain=False)
                        self.ble_lock.release()
                        continue
                    except Exception:
                        self.ble_lock.release()
                        raise
                elif not device.client.is_connected:
                    devices_to_pop.append(device.name)
                    self.mqtt.publish(
                        str(CnzDefinitions.DEVICE_TOPIC) + "/" + device.name.upper() + "/" + str(CnzDefinitions.STATUS),
                        str(CnzDefinitions.DISCONNECTED),
                        retain=False)

            if devices_to_pop:
                [self.devices.pop(device) for device in devices_to_pop]
                self.logger.info(f"Devices: {self.devices}")

    async def enable_notifications(self):
        if not self.devices or self.ble_lock.locked():
            await asyncio.sleep(NO_BLOCK_TIMEOUT)
        else:
            self.ble_lock.acquire()
            for device in self.devices.values():
                if device.client is not None and not device.notify_enabled and device.client.is_connected:
                    try:
                        await device.client.start_notify(device.command_api_uri, self.notify_callback)
                        await device.client.start_notify(device.notify_uri, self.notify_callback)
                        device.notify_enabled = True
                        await asyncio.sleep(NO_BLOCK_TIMEOUT)
                    except Exception:
                        self.ble_lock.release()
                        raise
            self.ble_lock.release()

    def queue_command(self, device_name, command_key, args):
        self._command_queue.put({str(CnzDefinitions.DEVICE_NAME): device_name,
                                 str(CnzDefinitions.COMMAND_KEY): command_key,
                                 str(CnzDefinitions.ARGS): args})

    async def dispatch_command(self):
        if not self._command_queue.empty() and not self.ble_lock.locked():
            self.ble_lock.acquire()
            command = self._command_queue.get(False)
            self.logger.info(f"Dispatching command {command}")
            # Parse the command: Device_name, command_key, args
            device_name = command.get(str(CnzDefinitions.DEVICE_NAME), None)
            command_key = command.get(str(CnzDefinitions.COMMAND_KEY), None)
            args = command.get(str(CnzDefinitions.ARGS), None)
            if None in [device_name, command_key, args]:
                self.logger.error(f"Invalid command: {command}")
                self.ble_lock.release()
                await asyncio.sleep(NO_BLOCK_TIMEOUT)
            device = self.devices.get(device_name, None)
            if device is None:
                self.logger.error(f"Device {device_name} not found")
                self.ble_lock.release()
                await asyncio.sleep(NO_BLOCK_TIMEOUT)
            if device.client is None or not device.client.is_connected:
                self.logger.error(f"Device {device_name} not connected")
                self.ble_lock.release()
                await asyncio.sleep(NO_BLOCK_TIMEOUT)
            else:
                await device.send_command(command_key, args)
                await asyncio.sleep(NO_BLOCK_TIMEOUT)
                self.ble_lock.release()
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
                await device.send_command(str(CnzDefinitions.SET_RELAY),
                                          {str(CnzDefinitions.RELAY_NUMBER): 1,
                                           str(CnzDefinitions.RELAY_STATE): True})
                await asyncio.sleep(2)
                await device.send_command(str(CnzDefinitions.SET_RELAY),
                                          {str(CnzDefinitions.RELAY_NUMBER): 1,
                                           str(CnzDefinitions.RELAY_STATE): False})
                for i in range(1, 24):
                    num = i % 4 + 1
                    self.logger.info(f"Sending command {num} to {device.name}")
                    await asyncio.sleep(0.25)
                    await device.send_command(str(CnzDefinitions.SET_RELAY),
                                              {str(CnzDefinitions.RELAY_NUMBER): num,
                                               str(CnzDefinitions.RELAY_STATE): True})
                    await asyncio.sleep(0.25)
                    await device.send_command(str(CnzDefinitions.SET_RELAY),
                                              {str(CnzDefinitions.RELAY_NUMBER): num,
                                               str(CnzDefinitions.RELAY_STATE): False})
            self.ble_lock.release()

    async def disconnect_devices(self):
        for device in self.devices.values():
            if device.client is not None:
                await device.client.__aexit__(None, None, None)

    def parse_notify_message(self, char_uuid, message):
        device_address = f'CENZ-{char_uuid[-8:].upper()}'
        self.logger.debug(f'message: {message}')
        if device_address in self.devices.keys() and sum(message[:-2]) % 256 == message[-1]:
            device = self.devices[device_address]
            notification_type = message[0]
            response_type = message[1]
            if notification_type == NotificationsEnum.NTFY_RESPONSE:
                if response_type == 9: # Relay response
                    output_number = int(message[2])
                    output_state = True if message[3] == 0x01 else False
                    self.mqtt.publish(
                        f'{CnzDefinitions.DEVICE_TOPIC}/{device.name.upper()}/{CnzDefinitions.OUTPUT}/{output_number}',
                        json.dumps({CnzDefinitions.ENABLED: output_state}),
                        retain=False
                    )
            elif notification_type == NotificationsEnum.NTFY_AMBIENT:

                temperature, humidity, luminosity, delta_t, version = struct.unpack('<fffIi', message[1:-2])
                self.logger.info(
                    f"Temperature: {temperature:.2f} C, Humidity: {humidity:.2f} %, Luminosity: {luminosity:.2f} lux, "
                    f"Delta T: {delta_t:.2f} s, Version: {version}")

                if temperature != 0:
                    self.mqtt.publish(
                        f'{CnzDefinitions.DEVICE_TOPIC}/{device.name.upper()}/{CnzDefinitions.AMBIENT}/'
                        f'{CnzDefinitions.TEMPERATURE}',
                        f"{temperature:.2f}",
                        retain=True
                    )


    def notify_callback(self, char_uuid, value):
        self.logger.debug("Notification from %s: %s", char_uuid, value)
        if value != bytearray(b'OK'):
            self.parse_notify_message(char_uuid.uuid, value)
