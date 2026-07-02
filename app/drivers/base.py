from app.drivers.android.android_driver import AndroidDriver
from app.drivers.driver import Driver
from app.drivers.harmony.harmony_driver import HarmonyDriver
from app.utils.exception_util import DeviceTypeKeyError

DRIVER_MAP = {
    "Android": AndroidDriver,
    "Harmony": HarmonyDriver
}


def create_driver(device_sn: str, device_type: str) -> Driver:
    device_class = DRIVER_MAP.get(device_type)
    if device_class is None:
        raise DeviceTypeKeyError(device_type)
    return device_class(device_sn)
