import subprocess as sp
import re
import traceback

from time import sleep

from app.utils.logger import std_logger


def send_hdc_command(shell_list: list):
    try:
        result = sp.run(shell_list, capture_output=True, text=True, check=True)
        sleep(0.1)
        return result
    except sp.CalledProcessError as e:
        std_logger.info(f"adb operate failed! error code: {e.returncode}, reason: {e.stderr.strip()},"
                        f"shell_list: {shell_list}, stack: {traceback.format_exc()}")
        raise e

def hdc_get_devices():
    """hdc获得设备列表"""
    shell_list = ['hdc', 'list', 'targets']
    result = sp.run(shell_list, capture_output=True, text=True)
    result_list = [i for i in result.stdout.split('\n') if i]
    device_list = [i.split('\t') for i in result_list]
    return device_list

def hdc_get_battery_level(device_id: str):
    """获取设备电量百分比"""
    try:
        shell_list = ['hdc', '-t', f'{device_id}', 'shell', 'hidumper', '-s', '3302', '-a', '-i']
        result = sp.run(shell_list,stdout=sp.PIPE,stderr=sp.STDOUT, text=True, check=True)
        output = result.stdout
        match = re.search(r'capacity:\s*(\d+)', output)
        if match:
            return int(match.group(1))
        else:
            std_logger.info("don't find capacity")
            return -1
    except Exception as e:
        std_logger.info(f'get battery level failed {str(e)}')
        return -1

def hdc_wakeup(device_id: str):
    shell_list = ['hdc', '-t', f'{device_id}', "shell", "power-shell", "wakeup"]
    send_hdc_command(shell_list)

def hdc_get_screen_state(device_id: str):
    shell_list = ['hdc', '-t', f'{device_id}', "shell", "hidumper", "-s", "3301", "-a", "-a"]
    result = send_hdc_command(shell_list).stdout

    screen_state = 'NA'
    match = re.search(r"Current State:\s*(\w+)", result)
    if match:
        screen_state = match.group(1)
    return screen_state