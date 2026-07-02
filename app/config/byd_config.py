
BYD_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.byd.aeri.caranywhere'
    },
    'my': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/btn_my'}
    },
    'my_car': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/llMypersonalCarHas'}
    },
    'vin_code_query': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/iv_car'}
    },
    'vin_code': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/tv_car_vin_value'}
    },
    'car': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/btn_car'}
    },
    'vpd': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/title_tv', 'text': '泊车代驾辅助'}
    },
    'vpd_safe_code': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/tv_title', 'text': '正在使用泊车代驾，请输入操作密码'}
    },
    'vpd_safe_code_input': {
        'xpath': '//*[@text="{}"]'
    },
    'vpd_loading': {
        'elements': {'resourceId': 'com.byd.aeri.caranywhere:id/textView', 'text': '正在加载...'}
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
    'start_enabled': {
        'elements': {'text': '开始', 'enabled': 'true'}
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
    'sentry_mode_status': {
        'xpath': '//*[@text="守卫中"]'
    },
    'open_sentry_mode': {
        'elements': {'resourceId': "app.huawei.motor:id/tv_sentry_mode"}
    },
    'confirm_sentry_mode': {
        'elements': {'resourceId': "app.huawei.motor:id/confirm_btn"}
    },
    'loading_sentry_mode': {
        'xpath': '//*[@text="开启中..."]'
    },
    'harmony_os': {
        'small_window': {
            'xpath': '//*[@text="鸿蒙智行"]/../..//*[@content-desc="点击启动悬浮窗"]'
        },
        'maximize_window': {
            'elements': {'resourceId': "androidhwext:id/hw_multiwindow_maximize_window"}
        },
    },
    'magic_os': {
        'small_window': {
            'xpath': '//*[@text="鸿蒙智行"]/../..//*[@content-desc="点击启动悬浮窗"]'
        },
        'maximize_window': {
            'elements': {'resourceId': "androidhnext:id/hw_multiwindow_maximize_window"}
        },
    },
    'hyper_os': {
        'app_name': {
            'elements': {'text': '鸿蒙智行'}
        },
        'small_window': {
            'xpath': '//*[@resource-id="com.miui.home:id/menu_item_small_window_container"]'
        },
        'bottom_nav_bar': {
            'xpath': '//*[@resource-id="android:id/navigationBarBackground'
        },
    },
    'miui': {
        'app_name': {
            'elements': {'text': '鸿蒙智行'}
        },
        'small_window': {
            'xpath': '//*[@resource-id="com.miui.home:id/menu_item_small_window_container"]'
        },
        'bottom_nav_bar': {
            'xpath': '//*[@resource-id="android:id/bottom_caption"]'
        },
    },
    'origin_os': {
        'app_name': {
            'elements': {'className': 'android.widget.TextView', 'resourceId': 'com.bbk.launcher2:id/label',
                         'text': '鸿蒙智行'}
        },
        'small_window': {
            'elements': {'text': '小窗'}
        },
        'maximize_window': {
            'elements': {'resourceId': "vivo:id/vigour_freeform_maximize"}
        },
    },
}


BYD_CONFIG_MAP = {
    'Android': BYD_ANDROID_CONFIG,
}

