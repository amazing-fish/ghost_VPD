VOYAH_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.voyah.fassistant'
    },
    'vin_code': {
        'xpath': "//*[string-length(@text)=21 and translate(@text, 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWX"
                 "YZ0123456789 ', '')='']"
    },
    'my': {
        'elements': {'resourceId': 'com.voyah.fassistant:id/tv_my', 'text': '我的'}
    },
    'my_car': {
        'elements': {'resourceId': 'com.voyah.fassistant:id/tv_name', 'text': '我的爱车'}
    },
    'my_car_query': {
        'elements': {'resourceId': 'com.voyah.fassistant:id/textview_content', 'text': '加载中'}
    },
    'car': {
        'elements': {'resourceId': 'com.voyah.fassistant:id/tv_h', 'text': '岚图'}
    },
    'vpd': {
        'elements': {'resourceId': 'com.voyah.fassistant:id/pst_iv_memory_parking', 'text': '泊车代驾'}
    },
    'vpd_loading': {
        'elements': {'resourceId': 'com.voyah.fassistant:id/textView', 'text': '正在加载...'}
    },
    'vpd_success': {
        'elements': {'text': '默认车位'}
    },
    'parking_space_input': {
        'xpath': '//*[@text="车要去哪儿"]'
    },
    'parking_space_select': {
        'xpath': "//*[contains(@text, '{}')]/../..//*[contains(@text, '去这里')]"
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
    'sentry_mode': {
        'xpath': '//*[@text="哨兵模式"]'
    },
    'open_sentry_mode': {
        'elements': {'resourceId': "com.voyah.fassistant:id/asm_tv_open"}
    },
    'confirm_sentry_mode': {
        'elements': {'resourceId': "android.widget.TextView", 'text': '继续'}
    },
    'ad_accept': {
        'xpath': '//*[@text="同意"]'
    }
}

VOYAH_CONFIG_MAP = {
    'Android': VOYAH_ANDROID_CONFIG
}
