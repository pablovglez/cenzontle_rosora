from threading import Lock, Thread
import asyncio
import sys
import os
import json
import logging
from gi.repository import GLib
from bleak.exc import BleakDBusError
from controller import BleManager
from manager import MqttManager
from utils.config_logger import LoggerManager


LOG_FORMAT = "%(asctime)s [ALLIANCE / %(module)s] [%(levelname)s] %(message)s"
DEFAULT_LOG_LEVEL_STR = "INFO"
LOG_LEVEL = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL_STR)
VERSION = "x.x.x"

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
        task2 = tg.create_task(monitored_task(manager.connect_devices, name="ClientTask", restart_delay=5, loop_delay=15))
        task3 = tg.create_task(monitored_task(manager.enable_notifications, name="EnableNotificationTask", restart_delay=5, loop_delay=15))
        task4 = tg.create_task(monitored_task(manager.dispatch_command, name="CommandTask", restart_delay=5, loop_delay=1))
        #task5 = tg.create_task(monitored_task(manager.test_connect_devices, name="TestConnectTask", restart_delay=5))

        await asyncio.Event().wait()

if __name__ == "__main__":
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)-15s %(name)-8s %(levelname)s: %(message)s",
    )

    logger_mgr = LoggerManager(name="Cenzontle", level=LOG_LEVEL,
                               service_type="Rosora")
    logger_mgr.config_logger()

    mqtt_mgr = MqttManager(logger_mgr=logger_mgr)
    mqtt_mgr.start()

    #ble_manager = BleManager(adapter="hci0", logger_mgr=logger_mgr)
    ble_manager = BleManager(adapter="hci1", logger_mgr=logger_mgr)

    def on_ble_command(client, userdata, message):
        topic = message.topic
        key_values = topic.split("/")
        device = key_values[2]
        message_payload = json.loads(message.payload.decode("utf-8"))
        print(f"Received message: {message.payload}")
        command_key = message_payload["command"]
        command_args = message_payload["args"]
        ble_manager.queue_command(device, command_key, command_args)

    mqtt_mgr.message_callback_add("mqttmanager/cenzontle/+/command", on_ble_command)
    # {
    # "device_name": "CENZ-0F8D17B6",
    """
    {
        "command": "set_relay",
        "args": {
            "relay_number": 1,
            "relay_state": false
        }
    }
    """

    try:
        asyncio.run(main(ble_manager))
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        ble_manager.unregister_agent()
    except BleakDBusError as e:
        if (e.dbus_error == "org.bluez.Error.ConnectionAttemptFailed" and
                "Page Timeout" in e.dbus_error_details):
            # Blocking error, no known solution, quit safely in the future
            logging.error(
                f"Connection attempt failed ({e.dbus_error} - {e.dbus_error_details}), quitting safely.")
            ble_manager.unregister_agent()
            sys.exit(0)
    except Exception:
        ble_manager.unregister_agent()
        raise
