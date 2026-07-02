import datetime
import os
import subprocess as sp
from time import sleep

from app.utils.logger import std_logger


def adb_start_call(device_id: str, phone_number: str):
    """打指定电话"""
    shell_list = ['adb', '-s',  f'{device_id}',  'shell', 'am', 'start', '-a', 'android.intent.action.CALL',
                  '-d', f'tel:{phone_number}']
    sp.run(shell_list)


def adb_stop_call(device_id: str):
    """停止打电话"""
    shell_list = ['adb', '-s', f'{device_id}', 'shell', 'input', 'keyevent', 'KEYCODE_ENDCALL']
    sp.run(shell_list, capture_output=True, text=True)


def adb_get_call_state(device_id: str):
    """监听来电状态"""
    output = sp.check_output(['adb', '-s', f'{device_id}', "shell", "dumpsys", "telephony.registry"],
                             universal_newlines=True, encoding='utf-8')
    for line in output.splitlines():
        if "mCallState" in line:
            return int(line.split("=")[1].strip())
    return -1


def adb_answer_call(device_id: str):
    """接听电话"""
    shell_list = ['adb', '-s', f'{device_id}', 'shell', 'input', 'keyevent', 'KEYCODE_CALL']
    sp.run(shell_list, capture_output=True, text=True)


def adb_massage(device_id: str, phone_number: str, massage: str):
    """发送短信至指定号码"""
    shell_list = ['adb', '-s', f'{device_id}', 'shell', 'am', 'start', '-a', 'android.intent.action.SENDTO',
                  '-d', f'sms:{phone_number}', '--es', f'sms_body', f"{massage}", '--ez', 'exit_on_sent', 'true']
    sp.run(shell_list)
    for i in [22, 66]:
        sleep(2)
        shell_list = ['adb', '-s', f'{device_id}', 'shell', 'input', 'keyevent', f'{i}']
        sp.run(shell_list, capture_output=True, text=True)
    sleep(1)


def adb_get_devices():
    """adb获得设备列表"""
    shell_list = ['adb', 'devices']
    result = sp.run(shell_list, capture_output=True, text=True)
    result_list = list(filter(lambda x: x and x != 'List of devices attached', result.stdout.split('\n')))
    device_list = [i.split('\t') for i in result_list]
    return device_list


def adb_get_device_file(device_id: str, file_path: str, target_path: str):
    """获取指定设备指定目录文件"""
    shell_list = ['adb', '-s', f'{device_id}', 'pull', f'{file_path}', f'{target_path}']
    result = sp.run(shell_list, capture_output=True, text=True).stdout
    is_success = result.find('No such file or directory') == -1
    return is_success


def adb_get_device_files_by_date(device_id: str, remote_dir: str, target_dir: str,
                                 start_date: str = None, end_date: str = None):
    """
    获取指定设备指定日期范围内的文件
    默认拉取当天的文件

    :param device_id: 设备ID
    :param remote_dir: 设备上的目录路径
    :param target_dir: 本地保存目录
    :param start_date: 起始日期(YYYY-MM-DD)，默认当天
    :param end_date: 结束日期(YYYY-MM-DD)，默认当天
    :return: 成功拉取的文件列表
    """
    # 设置默认日期范围（当天）
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    start_date = start_date or today
    end_date = end_date or today

    # 获取日期范围内的文件列表
    date_filter_cmd = [
        'adb', '-s', device_id, 'shell',
        f'find "{remote_dir}" -type f -newermt "{start_date} 00:00:00" ! -newermt "{end_date} 23:59:59"'
    ]

    try:
        result = sp.run(date_filter_cmd, capture_output=True, text=True, check=True)
        remote_files = result.stdout.splitlines()
    except sp.CalledProcessError as e:
        std_logger.info(f"Error finding files: {e.stderr}")
        return []

    # 创建本地目标目录
    os.makedirs(target_dir, exist_ok=True)

    # 拉取符合条件的文件
    success_files = []
    for remote_file in remote_files:
        if not remote_file.strip():
            continue

        pull_cmd = [
            'adb', '-s', device_id,
            'pull', remote_file.strip(),
            target_dir
        ]

        try:
            result = sp.run(pull_cmd, capture_output=True, text=True, check=True)
            if '0 skipped' in result.stdout:
                success_files.append(remote_file)
        except sp.CalledProcessError as e:
            std_logger.info(f"Error pulling {remote_file}: {e.stderr}")

    return success_files

def adb_get_battery_level(device_id: str):
    """获取当前手机电量百分比"""
    shell_list = ['adb', '-s', f'{device_id}', "shell", "dumpsys", "battery"]
    result = sp.run(shell_list, capture_output=True, text=True).stdout
    for line in result.splitlines():
        if "level:" in line:
            level = int(line.strip().split()[1])
            return level
    return -1

def adb_input_tap(device_id: str, x, y):
    shell_list = ['adb', '-s', f'{device_id}', "shell", "input", "tap", f"{x}", f"{y}"]
    sp.run(shell_list, capture_output=True, text=True)