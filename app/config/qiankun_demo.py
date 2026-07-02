QIANKUN_DEMO_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.huawei.adsapp'
    },
    'vpd_title': {
        'xpath': '//*[@resource-id="com.huawei.adsapp:id/demo_select_title"]'
    },
    'env_select': {
        'xpath': '//*[@text="云端环境"]'
    },
    'env_pre': {
        'xpath': '//*[@text="pre"]'
    },
    'real_function': {
        'xpath': '//*[@text="实用功能"]'
    },
    'l4_ghost_button': {
        'xpath': '//*[@resource-id="com.huawei.adsapp:id/demo_veh_more_func_l4ghost_control_switch"]'
    },
    'vehicle_loading': {
        'xpath': '//*[@text="正在加载车辆信息..."]'
    },
    'vpd': {
        'xpath': '//*[@text="进入VPD"]'
    },
    'vpd_safe_code': {
        'elements': {'text': '请输入安全码'}
    },
    'vpd_safe_code_input': {
        'elements': {'className': 'android.widget.EditText', 'text': ''}
    },
    'vpd_loading': {
        'elements': {'text': '正在加载...'}
    },
    'vpd_success': {
        'elements': {'text': '默认车位'}
    },
    'parking_space_input': {
        'xpath': '//*[@text="输入车位号设为目的地"]'
    },
    'parking_space_select': {
        'xpath': "//*[contains(@text, '{}')]/../..//*[contains(@text, '去这里')]"
    },
    'l4_activate_btn': {
        'xpath': '//*[@resource-id="com.huawei.adsapp:id/l4_activate_btn"]'
    },
    'start_loading': {
        'elements': {'text': '路线规划中，请稍后…'}
    },
    'parking': {
        'elements': {'text': '泊入车位'}
    },
    'summon': {
        'elements': {'text': '靠边停车'}
    },
    'main_title': {
        'xpath': "//*[contains(@text, '{}')]"
    },
    'sub_title': {
        'xpath': "//*[contains(@text, '{}')]/following-sibling::*"
    },
    'retry': {
        'elements': {'text': '重试'},
    },
    'start': {
        'xpath': '//*[@text="开始"]'
    },
    'start_enabled': {
        'elements': {'resourceId': 'com.huawei.adsapp:id/base_btn_text', 'text': '开始'},
        'judge_enable': True
    },
    'start_btn': {
        'xpath': '//*[@resource-id="com.huawei.adsapp:id/cl_park_btn_start"]'
    },
    'confirm': {
        'xpath': '//*[@text="确定"]'
    },
    'pause': {
        'xpath': '//*[@text="暂停"]'
    },
    'quit': {
        'xpath': '//*[@text="退出"]'
    },
    'continue': {
        'xpath': '//*[@text="继续"]'
    },
    'finish': {
        'xpath': '//*[@text="返回"]'
    },
    'speed': {
        'xpath': '//*[contains(@text, "km/h")]'
    },
    'driver_distance': {
        'xpath': '//*[@text="行驶距离"]/../Row[1]/*[@text="米"]/preceding-sibling::*[1]'
    },
    'min_total_cost': {
        'xpath': '//*[@text="总用时"]/../Row[1]/*[@text="分"]/preceding-sibling::*[1]'
    },
    'sec_total_cost': {
        'xpath': '//*[@text="总用时"]/../Row[1]/*[@text="秒"]/preceding-sibling::*[1]'
    },
    'min_parking_cost': {
        'xpath': '//*[@text="泊车用时"]/../Row[1]/*[@text="分"]/preceding-sibling::*[1]'
    },
    'sec_parking_cost': {
        'xpath': '//*[@text="泊车用时"]/../Row[1]/*[@text="秒"]/preceding-sibling::*[1]'
    },
    'text_equal_xpath': {
        'xpath': '//*[@text="{}"]'
    },
}


QIANKUN_DEMO_CONFIG_MAP = {
    'Android': QIANKUN_DEMO_ANDROID_CONFIG
}
