AVATAR_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.avatar.buyer.client'
    },
    'more_menu': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/fl_more_menu'}
    },
    'vin_code': {
        'xpath': '//*[@text="车架号"]/following-sibling::*'
    },
    'car': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/rb_car', 'text': '车'}
    },
    'vpd_old': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/tv_title', 'text': '泊车代驾VPD'}
    },
    'vpd_new': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/tv_title', 'text': '泊车代驾辅助'}
    },
    'vpd_loading': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/textView', 'text': '正在加载...'}
    },
    'all_control': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/tv_all_control', 'text': '全部功能'}
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
    'clear_safe_code': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/iv_clear'}
    },
    'ad_confirm': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/tv_confirm'}
    },
    'ad_close': {
        'elements': {'resourceId': 'com.avatar.buyer.client:id/iv_close'}
    },
    'ad_accept': {
        'xpath': '//*[@text="同意"]'
    },
    'sentry_mode': {
        'xpath': '//*[@text="哨兵模式"]'
    },
    'sentry_mode_status': {
        'xpath': '//*[@text="已开启"]'
    },
    'open_sentry_mode': {
        'xpath': '//*[@text="开启哨兵模式"]'
    },
    'confirm_sentry_mode': {
        'xpath': '//*[@text="我知道了"]'
    },
    'close_sentry_mode': {
        'xpath': '//*[@text="关闭哨兵模式"]'
    },
    'close_confirm_sentry_mode': {
        'xpath': '//*[@text="确认关闭"]'
    },
}

AVATAR_CONFIG_MAP = {
    'Android': AVATAR_ANDROID_CONFIG
}
