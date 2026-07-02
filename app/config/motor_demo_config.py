
MOTOR_DEMO_HARMONY_CONFIG = {
    'package': {
        'package_name': 'adsmobilesdk.aito.huawei'
    },
    'app_loading': {
        'xpath': '//*[@text="正在加载..."]'
    },
    'vin_code': {
        'xpath': "//*[string-length(@text)=17 and translate(@text, 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXY"
                 "Z0123456789', '')='']",
        'if_fresh': True
    },
    'vpd_select': {
        'elements': {'text': 'VPD'}
    },
    'vehicle_select': {
        'elements': {'text': '请选择对应的车型'}
    },
    'env_select': {
        'elements': {'text': '请选择对应的环境'}
    },
    'init': {
        'elements': {'text': '初始化'}
    },
    'vin_select': {
        'elements': {'text': '请选择正确的Vin码'}
    },
    'vpd': {
        'elements': {'text': '进入泊车代驾'}
    },
    'vpd_loading': {
        'xpath': '//*[@text="正在加载..."]'
    },
    'vpd_success': {
        'xpath': '//*[@text="输入目的地"]'
    },
    'parking_space_input': {
        'xpath': '//*[@text="车要去哪儿"]'
    },
    'parking_space_input_text': {
        'xpath': '//*[@id="search_input"]'
    },
    'parking_space_select': {
        'xpath': "//*[contains(@text, '{}')]/../..//*[contains(@text, '去这里')]"
    },
    'start_loading': {
        'xpath': '//*[@text="路线规划中，请稍后…"]'
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
        'xpath': '//*[@text="重试"]',
    },
    'start': {
        'xpath': '//*[@text="开始"]'
    },
    'start_enabled': {
        'xpath': '//root[1]/Stack[1]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]/Column[1]'
                 '/Column[1]/Button[@clickable="true"]'
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
    'remain_distance': {
        'xpath': '//*[contains(@text, "剩余")]'
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
    'sentry_mode': {
        'xpath': '//*[@text="哨兵模式"]'
    },
    'sentry_mode_status': {
        'xpath': '//*[@text="守卫中"]'
    },
    'open_sentry_mode': {
        'xpath': '//*[@text="立即开启"]'
    },
    'confirm_sentry_mode': {
        'xpath': '//*[@text="确定"]'
    },
    'loading_sentry_mode': {
        'xpath': '//*[@text="开启中..."]'
    }
}

MOTOR_DEMO_CONFIG_MAP = {
    'Harmony': MOTOR_DEMO_HARMONY_CONFIG
}

