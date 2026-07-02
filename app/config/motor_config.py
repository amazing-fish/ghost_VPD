MOTOR_ANDROID_CONFIG = {
    'package': {
        'package_name': 'app.huawei.motor'
    },
    'app_loading': {
        'elements': {'resourceId': 'app.huawei.motor:id/progress_text', 'text': '正在加载...'}
    },
    'my': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/app_hw_bottom_main"]/android.widget.LinearLayout[5]'
    },
    'my_car': {
        'elements': {'resourceId': 'app.huawei.motor:id/tv_car_manager', 'text': '我的车辆'}
    },
    'my_car_query': {
        'elements': {'resourceId': 'app.huawei.motor:id/loading_tips_htv', 'text': '查询中...'}
    },
    'vin_code': {
        'elements': {'resourceId': 'app.huawei.motor:id/copy_content_htv', 'index': 0}
    },
    'vin_last_6': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/Car_Six_id"]'
    },
    'vin_code_new': {
        'xpath': '//*[contains(@text, "{}")]'
    },
    'car': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/app_hw_bottom_main"]/android.widget.LinearLayout[3]'
    },
    'vpd': {
        'elements': {'resourceId': 'app.huawei.motor:id/vehicle_list_double_text_up', 'text': '泊车代驾'}
    },
    'vpd_safe_code': {
        'elements': {'resourceId': 'app.huawei.motor:id/text_title', 'text': '请输入安全码'}
    },
    'vpd_safe_code_input': {
        'elements': {'className': 'android.widget.EditText', 'text': ''}
    },
    'vpd_loading': {
        'elements': {'resourceId': 'app.huawei.motor:id/uavp_epa_textView', 'text': '正在加载...'}
    },
    'vpd_success': {
        'elements': {'text': '默认车位'}
    },
    'parking_space_input': {
        'xpath': '//*[@text="车要去哪儿"]'
    },
    'parking_space_input_new': {
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
    'start_enabled': {
        'elements': {'resourceId': 'app.huawei.motor:id/base_btn_text', 'text': '开始'},
        'judge_enable': True
    },
    'start_btn': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/cl_park_btn_start"]'
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
    'sentry_mode': {
        'xpath': '//*[@text="哨兵模式"]'
    },
    'sentry_mode_status': {
        'xpath': '//*[@text="守卫中"]'
    },
    'sentry_mode_status_new': {
        'xpath': '//*[@text="哨兵模式开启"]'
    },
    'sentry_mode_status_off': {
        'xpath': '//*[@text="未开启"]'
    },
    'open_sentry_mode': {
        'elements': {'resourceId': "btn_sentry_button"}
    },
    'confirm_sentry_mode': {
        'xpath': '//*[@text="确定"]'
    },
    'loading_sentry_mode': {
        'xpath': '//*[@text="开启中..."]'
    },
    'close_sentry_mode': {
        'elements': {'resourceId': "btn_sentry_button"}
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
    'cruise_tip': {
        'elements': {'resourceId': "app.huawei.motor:id/cl_park_tv_title"}
    },
    'parking_lot': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/vpd_title"]//android.widget.TextView[1]'
    },
    'floor': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/avp_floor_prev_img"]/following-sibling::*[1]'
    },
    'total_spots': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/tv_total_parking_spots"]'
    },
    'default_parking_space': {
        'xpath': '//*[@text="默认车位"]/preceding-sibling::*[1]'
    },
    'default_parking_space_no_set': {
        'xpath': '//*[@text="默认车位"]/following-sibling::*[1]'
    },
    'default_boarding_spot': {
        'xpath': '//*[@text="默认上车点"]/preceding-sibling::*[1]'
    },
    'default_boarding_spot_no_set': {
        'xpath': '//*[@text="默认上车点"]/following-sibling::*[1]'
    },
    'right_icon_btn': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/fl_preview"]'
    },
    'favorite_flag': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/frequent_place_card"]//*[@text="去这里"]'
    },
    'favorite_empty_flag': {
        'xpath': '//*[@text="可在地图中点选并收藏车位和停车点"]'
    },
    'full_view_btn': {
        'xpath': '//*[@text="全览"]'
    },
    'ego_view_btn': {
        'xpath': '//*[@text="回自车"]'
    },
    'assess_info': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/mine_nickName"]'
    },
    'assess_icon': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/mine_portrait"]'
    },
    'account_btn': {
        'xpath': '//*[@resource-id="com.huawei.hms.account:id/other_btn_login"]'
    },
    'number_input':{
        'xpath':'//*[@text="登录"]/android.view.View[2]/android.view.View[1]/android.view.View[1]'
    },
    'pwd_input':{
        'xpath':'//*[@text="登录"]/android.view.View[2]/android.view.View[2]/android.widget.EditText[1]'
    },
    'login_btn':{
        'xpath':'//*[@text="登录"]/android.view.View[5]'
    },
    'online_confirm':{
        'xpath':'//*[@resource-id="app.huawei.motor:id/dialog_right_btn"]'
    },

    'car_select': {
        'xpath': '//*[@resource-id="app.huawei.motor:id/app_fragment_container"]/android.widget.FrameLayout[1]/'
                 'android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/'
                 'android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/'
                 'android.view.View[1]/android.view.View[1]/android.view.View[1]/android.widget.ViewGroup[1]/'
                 'android.widget.ViewGroup[1]/android.widget.ViewGroup[1]'
    },
    'car_vin': {
        'xpath': '//*[@text="车架号 %s(后六位)"]'
    },
    'settings':{
        'xpath':'//*[contains(@text,"设置")]'
    },
    'logout':{
        'xpath':'//*[@resource-id="app.huawei.motor:id/vehicle_ac_appointments_new_btn"]'
    },
    'logout_confirm':{
        'xpath':'//*[@resource-id="app.huawei.motor:id/positive_htv"]'
    },
    'park_location': {
        'elements': {'resourceId': 'app.huawei.motor:id/cl_select_tv_name'}
    },
    'view_name': {
        'elements': {'resourceId': 'app.huawei.motor:id/direct_textview'}
    },
    'i_know_button': {
        'xpath': '//*[@text="知道了"]'
    },
    'confirm_button': {
        'xpath': '//*[@text="确认"]'
    },
}

MOTOR_HARMONY_CONFIG = {
    'package': {
        'package_name': 'app.huawei.hmos.motor'
    },
    'app_loading': {
        'xpath': '//*[@text="正在加载..."]'
    },
    'my': {
        'elements': {'text': '我的'}
    },
    'my_car': {
        'elements': {'text': '我的车辆'}
    },
    'my_car_query': {
        'elements': {'text': '查询中...'}
    },
    'vin_code': {
        'xpath': "//*[string-length(@text)=17 and translate(@text, 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXY"
                 "Z0123456789', '')='']",
        'if_fresh': True
    },
    'vin_last_6': {
        'xpath': "//*[contains(@text, '车架号')]"
    },
    'vin_code_new': {
        'xpath': '//*[contains(@text, "{}")]'
    },
    'car': {
        'xpath': '//*[@text="爱车"]'
    },
    'vpd': {
        'elements': {'text': '泊车代驾'}
    },
    'vpd_safe_code': {
        'xpath': '//*[@text="请输入安全码"]'
    },
    'vpd_safe_code_input': {
        'xpath': '//*[@id="SafeCodeInputVM"]'
    },
    'vpd_loading': {
        'xpath': '//*[@text="正在加载..."]'
    },
    'vpd_success': {
        'xpath': '//*[@text="默认车位"]'
    },
    'parking_space_input': {
        'xpath': '//*[@text="输入车位号设为目的地"]'
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
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]'
                 '/Column[1]/Column[1]/GridRow[1]/GridCol[2]/Button[@opacity="1.000000"]'
    },
    'start_btn': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]'
                 '/Column[1]/Column[1]/GridRow[1]/GridCol[2]/Button[1]'
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
    'sentry_mode_status_new': {
        'xpath': '//*[@text="哨兵模式开启"]'
    },
    'sentry_mode_status_off': {
        'xpath': '//*[@text="未开启"]'
    },
    'open_sentry_mode': {
        'xpath': '//*[@text="立即开启"]'
    },
    'confirm_sentry_mode': {
        'xpath': '//*[@text="确定"]'
    },
    'loading_sentry_mode': {
        'xpath': '//*[@text="开启中..."]'
    },
    'close_sentry_mode': {
        'xpath': '//*[@text="立即关闭"]'
    },
    'cruise_tip': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]'
                 '/Column[1]/Column[1]/Column[1]/GridRow[1]/GridCol[1]/Row[1]/Column[1]/Text[1]'
    },
    'parking_lot': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]'
                 '/Column[1]/Column[1]/Column[1]/Column[1]/Column[1]/Row[1]/Text[1]'
    },
    'floor': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[1]/RelativeContainer[1]'
                 '/Column[1]/Column[1]/Scroll[1]/Column[1]//Image/following-sibling::*[1]'
    },
    'total_spots': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]'
                 '/Column[1]/Column[1]/Column[1]/Column[1]/Column[1]/Row[1]/Row[1]/Text[1]'
    },
    'default_parking_space': {
        'xpath': '//*[@text="默认车位"]/preceding-sibling::*[1]'
    },
    'default_parking_space_no_set': {
        'xpath': '//*[@text="默认车位"]/following-sibling::*[1]'
    },
    'default_boarding_spot': {
        'xpath': '//*[@text="默认上车点"]/preceding-sibling::*[1]'
    },
    'default_boarding_spot_no_set': {
        'xpath': '//*[@text="默认上车点"]/following-sibling::*[1]'
    },
    'right_icon_btn': {
        'xpath': '//*[@id="right_icon_btn"]'
    },
    'favorite_flag': {
        'xpath': '//*[@text="常去地点"]/../following-sibling::Column[1]//*[@text="去这里"]'
    },
    'favorite_empty_flag': {
        'xpath': '//*[@text="暂无常去地点，您可在地图中收藏车位和上下车点"]'
    },
    'full_view_btn': {
        'xpath': '//*[@text="全览"]'
    },
    'ego_view_btn': {
        'xpath': '//*[@text="回自车"]'
    },
    'assess_info': {
        'xpath': '//root[1]/Stack[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                 'NavDestinationContent[1]/Stack[1]/Column[1]/Tabs[1]/Swiper[1]/TabContent[1]/'
                 'Column[1]/List[1]/ListItemGroup[1]/ListItem[1]/Column[1]/Button[1]/Row[1]/Column[1]//Text'
    },
    'assess_icon': {
        'xpath': '//root[1]/Stack[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                 'Stack[1]/Column[1]/Tabs[1]/Swiper[1]/TabContent[1]/Column[1]/List[1]/ListItemGroup[1]/ListItem[1]/'
                 'Column[1]/Button[1]/Row[1]/Image[1]'
    },
    'account_btn': {
        'xpath': '//root[1]/Dialog[1]/Column[1]/Column[1]/Stack[1]/GridRow[1]/GridCol[1]/Row[1]/Column[1]/'
                 'Column[1]/Column[1]/Button[2]'
    },
    'number_input':{
        'xpath':'//root[1]/Stack[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                'NavDestinationContent[1]/Column[1]/Stack[1]/Web[1]/rootWebArea[1]/sectionWithoutName[1]/'
                'genericContainer[1]/genericContainer[1]'
    },
    'manual_btn': {
        'xpath': '//*[@text="手动输入"]'
    },
    'pwd_input':{
        'xpath':'//root[1]/Stack[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                'NavDestinationContent[1]/Column[1]/Stack[1]/Web[1]/rootWebArea[1]/sectionWithoutName[1]/'
                'genericContainer[2]/textField[1]'
    },
    'login_btn':{
        'xpath':'//root[1]/Stack[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                'NavDestinationContent[1]/Column[1]/Stack[1]/Web[1]/rootWebArea[1]/genericContainer[4]',
    },
    'update_btn':{
        'xpath':'//*[@text="更新"]',
    },
    'car_select': {
        'xpath': '//root[1]/Stack[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                 'NavDestinationContent[1]/Stack[1]/Column[1]/Tabs[1]/Swiper[1]/TabContent[1]/'
                 'Stack[1]/__Common__[1]/Stack[1]/__Common__[1]/Column[1]/Row[1]/Row[1]'
    },
    'car_vin': {
        'xpath': '//*[@text="车架号 %s(后六位)"]'
    },
    'settings':{
        'xpath':'//*[@text="设置"]',
    },
    'logout':{
        'xpath':'//*[@text="退出登录"]',
    },
    'logout_confirm':{
        'xpath':'//root[1]/Dialog[1]/Column[1]/__Common__[1]/Stack[1]/GridRow[1]/GridCol[1]/Row[1]/'
                'Column[1]/Column[1]/Row[1]/Button[2]',
    },
    'park_location': {
        'xpath': '//*[@text="停泊位置"]/following-sibling::*[1]'
    },
    'view_name': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]/Column[1]'
                 '/Column[2]/List[1]/ListItem[3]/Column[1]/Stack[1]/Column[1]/Column[1]/Row[1]/Swiper[1]/Column[1]'
                 '/Row[1]/Image[2]/following-sibling::*[1]'
    },
    'i_know_button': {
        'xpath': '//*[@text="知道了"]'
    },
    'confirm_button': {
        'xpath': '//*[@text="确认"]'
    },
    'next_btn': {
        'xpath': '//*[@text="下次再说"]'
    }
}

MOTOR_CONFIG_MAP = {
    'Android': MOTOR_ANDROID_CONFIG,
    'Harmony': MOTOR_HARMONY_CONFIG
}
