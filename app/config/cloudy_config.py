CLOUDY_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.cloudy.component'
    },
    'service': {
        'elements': {'resourceId': 'com.cloudy.component:id/iv_text', 'text': '服务'}
    },
    'car': {
        'xpath': '//*[@resource-id="com.cloudy.component:id/tab_love_car"]/android.widget.LinearLayout[1]'
                 '/android.widget.FrameLayout[2]'
    },
    'identity_skip': {
        'elements': {'resourceId': 'com.cloudy.component:id/tv_skip', 'text': '跳过'}
    },
    'more_car': {
        'elements': {'resourceId': 'com.cloudy.component:id/iv_car_more'}
    },
    'default_car': {
        'xpath': '//*[@resource-id="com.cloudy.component:id/cl_current_car"]'
    },
    'btn_look': {
        'xpath': '//*[@resource-id="com.cloudy.component:id/btn_look"]'
    },
    'vin_code': {
        'elements': {'resourceId': 'com.cloudy.component:id/tv_value', 'index': 1}
    },
    'vpd': {
        'elements': {'text': '泊车代驾辅助'}
    },
    'vpd_loading': {
        'elements': {'resourceId': 'com.cloudy.component:id/textView', 'text': '正在加载...'}
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
    'start_loading': {
        'elements': {'text': '路线规划中，请稍后…'}
    },
    'parking': {
        'elements': {'text': '泊入车位'}
    },
    'summon': {
        'elements': {'text': '在这上车'}
    },
    'vpd_safe_code': {
        'xpath': '//*[@text="验证安全码"]'
    },
    'vpd_safe_code_input': {
        'xpath': '//*[@resource-id="com.cloudy.component:id/edit_text_view"]'
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
    'parking_finish': {
        'elements': {'text': '车已泊入车位'}
    },
    'summon_finish': {
        'elements': {'text': '车已到达上车点'}
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
}

CLOUDY_CONFIG_MAP = {
    'Android': CLOUDY_ANDROID_CONFIG
}
