from __future__ import annotations
import json
from typing import Iterable, Type, Optional, TypeVar
from peewee import CharField, Model, AutoField, DateTimeField, SQL, ForeignKeyField, \
    BooleanField, TextField, IntegerField, SmallIntegerField, FloatField
from app.models.mysql import db
from json import dumps, loads

from app.utils.gateway_db_util import DataAccessor

T = TypeVar('T', bound='BaseModel')


class BaseModel(Model):
    """表基类"""
    id = AutoField()  # 主键
    create_time = DateTimeField(constraints=[SQL('DEFAULT CURRENT_TIMESTAMP')])  # 创建时间
    update_time = DateTimeField(constraints=[SQL('DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')])  # 更新时间

    _accessor = None  # 访问数据库

    class Meta:
        database = db
        legacy_table_names = False
        only_save_dirty = True

    @classmethod
    def gateway(cls):
        if cls._accessor is None:
            cls._accessor = DataAccessor(cls)
        return cls._accessor

    @classmethod
    def exists_by_id(cls, _id: [int, str]) -> bool:
        """根据主键判断是否存在"""
        return cls.select().where(cls.id == _id).exists()

    @classmethod
    def get_by_id(cls: Type[T], _id: [int, str], only: Iterable = None, err_msg: str = None) -> Optional[T]:
        """根据主键获取
        Args:
            _id: 主键
            only: 仅包含在内的字段名列表
            err_msg: 错误描述

        Raises:
            RuntimeError
        """
        fields = [getattr(cls, f) for f in only] if only else []
        try:
            return cls.select(*fields).where(cls.id == _id).get()
        except cls.DoesNotExist:
            raise RuntimeError(err_msg if err_msg else 'does not exist')

    @classmethod
    def _excluded_field_names(cls) -> set:
        """转换为dict时排除在外的字段名"""
        return set()

    @classmethod
    def _extra_attr_names(cls) -> set:
        """转换为dict时额外增加的属性名"""
        return set()

    def to_dict(self, *, only: Iterable = None, exclude: Iterable = None, allow_extra: bool = True,
                extra: Iterable = None, ignore_id: bool = False, recurse: bool = False, recurse_depth: int = None,
                recurse_allow_extra: bool = False) -> dict:
        """转换为dict

        Args:
            only: 仅包含在内的字段名列表
            exclude: 排除在外的字段名列表
            allow_extra: 是否允许额外增加属性
            extra: 额外增加的属性名列表
            ignore_id: 是否忽略id
            recurse: 是否对外键进行递归转换
            recurse_depth: 递归深度，默认无限制
            recurse_allow_extra: 递归转换时是否允许额外增加属性
        """
        only = set(only or [])
        exclude = set(exclude or []) | self._excluded_field_names()
        if ignore_id:
            exclude.add('id')
        if recurse_depth is None:
            recurse_depth = -1
        if recurse_depth == 0:
            recurse = False
        data = {}

        # field
        field_set = set(self.__data__) - exclude
        if field_set and only:
            field_set &= only
        for f in field_set:
            f_data = self.__data__[f]
            if recurse and f_data is not None and isinstance(self._meta.fields[f], ForeignKeyField):
                rel_obj = getattr(self, f)
                f_data = rel_obj.to_dict(allow_extra=recurse_allow_extra, ignore_id=ignore_id, recurse=recurse,
                                         recurse_depth=recurse_depth - 1, recurse_allow_extra=recurse_allow_extra)
            data[f] = f_data

        # attr
        if allow_extra:
            attr_set = (set(extra or []) | self._extra_attr_names()) - exclude
            if attr_set and only:
                attr_set &= only
            for attr in attr_set:
                fn_or_val = getattr(self, attr)
                data[attr] = fn_or_val() if callable(fn_or_val) else fn_or_val
        return data

    def save(self, lock_ut: bool = False, **kwargs) -> int:
        """持久化到数据库

        Args:
            lock_ut: 不改变更新时间
        """
        if lock_ut:
            self._dirty.add('update_time')
        return super().save(**kwargs)


class JSONCharField(CharField):
    """JSON字段（VARCHAR）"""

    def db_value(self, value):
        if value is not None:
            return dumps(value, ensure_ascii=False, sort_keys=True)

    def python_value(self, value):
        if value is not None:
            return loads(value)


class JSONTextField(TextField):
    """JSON字段（TEXT）"""

    def db_value(self, value):
        if value is not None:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def python_value(self, value):
        if value is not None:
            return json.loads(value)


class VpdTestTask(BaseModel):

    FUNCTION_MAP = {
        'parking': '代客泊车',
        'summon': '智能召唤'
    }

    NOTICE_MAP = {
        1: '小鲁班通知',
        2: '电话/短信通知'
    }

    CASE_TYPE_MAP = {
        1: '巡航暂停继续用例',
        2: '巡航暂停退出用例',
        3: '泊车哨兵模式用例',
        4: '巡航切后台用例',
        5: '巡航接打电话用例',
        6: '巡航小窗口用例',
        7: '弱网模式用例',
        8: '巡航杀后台用例',
        9: '泊车开关哨兵用例',
        10: '代客充电用例',
        11: '上电成功率用例',
        12: '哨兵衔接VPD用例',
        13: '位置图片楼层用例'
    }

    SCENE_TYPE_MAP = {
        1: '泊入',
        2: '泊出',
        3: '脱困',
        4: '临停等待',
        5: '临停',
        6: '巡航'
    }

    task_name = CharField(constraints=[SQL('comment \'任务名称\'')], max_length=255)
    route_list = JSONTextField(default=list, constraints=[SQL('comment \'路线列表\'')])
    parking_lot = CharField(constraints=[SQL('comment \'停车场\'')], max_length=255)
    log_info = JSONTextField(default=list, constraints=[SQL('comment \'日志信息\'')])
    executor_ip = CharField(constraints=[SQL('comment \'执行机ip\'')], max_length=20)

    execute_type = SmallIntegerField(default=1, constraints=[SQL('comment \'运行方式 1 按时间 2 按次数\'')])
    n_execute = IntegerField(default=0, constraints=[SQL('comment \'时间(min)or次数\'')])

    vehicle_wait_time = IntegerField(default=1, constraints=[SQL('comment \'车辆泊车休眠时间\'')])
    vehicle_wait_time_summon = IntegerField(default=1, constraints=[SQL('comment \'车辆召唤休眠时间\'')])

    is_record_video = BooleanField(default=False, constraints=[SQL('comment \'是否录制视频\'')])
    start_time = DateTimeField(constraints=[SQL('comment \'启动时间\'')])
    notice_type = IntegerField(default=0, constraints=[SQL('comment \'接管通知类型\'')])
    emergency_number = CharField(default='', constraints=[SQL('comment \'紧急联系号码\'')], max_length=20)
    device_info = JSONTextField(default={}, constraints=[SQL('comment \'设备信息\'')])
    case_type = IntegerField(constraints=[SQL('comment \'用例类型\'')], default=0)
    case_scene_ls = JSONTextField(default=list, constraints=[SQL('comment \'用例场景\'')])
    case_interval_time = IntegerField(constraints=[SQL('comment \'用例间隔时间(s)\'')], default=0)
    case_execute_nums = IntegerField(constraints=[SQL('comment \'用例执行次数\'')], default=0)

    username = CharField(constraints=[SQL('comment \'创建人\'')], max_length=50)
    status = SmallIntegerField(default=-1, constraints=[SQL('comment \'任务状态 1未启动 0运行中 1已完成 2异常中断 3 停止中'
                                                            ' 4 停止\'')])
    reason = CharField(default='', constraints=[SQL('comment \'错误原因\'')])

    device_list = JSONTextField(default=list, constraints=[SQL('comment \'多设备+APP列表\'')])

    class Meta:
        table_name = 'vpd_test_task'

    def update_status(self, status: int, reason: str = '') -> int:
        self.status = status
        self.reason = str(reason)
        return self.save()


class VpdTestStats(BaseModel):
    task_id = ForeignKeyField(VpdTestTask)

    vin_code = CharField(constraints=[SQL('comment \'车架号\'')], max_length=50)
    vehicle_name = CharField(constraints=[SQL('comment \'车辆名称\'')], max_length=50, default='')
    app_name = CharField(constraints=[SQL('comment \'app名称\'')], max_length=50)
    app_version = CharField(constraints=[SQL('comment \'app版本\'')], max_length=50)
    ads_version = CharField(constraints=[SQL('comment \'ads版本\'')], max_length=255, default='')
    device_type = CharField(constraints=[SQL('comment \'设备类型\'')], max_length=20)
    function = CharField(constraints=[SQL('comment \'功能\'')])

    n_park_or_summon = IntegerField(default=0, constraints=[SQL('comment \'泊入/泊出次数\'')])
    n_success_park_or_summon = IntegerField(default=0, constraints=[SQL('comment \'泊入/泊出成功次数\'')])
    n_calculate_road = IntegerField(default=0, constraints=[SQL('comment \'算路次数\'')])
    n_success_calculate_road = IntegerField(default=0, constraints=[SQL('comment \'算路成功次数\'')])
    n_cruise = IntegerField(default=0, constraints=[SQL('comment \'巡航次数\'')])
    n_cruise_quit = IntegerField(default=0, constraints=[SQL('comment \'巡航退出次数\'')])

    rate_park_or_summon = FloatField(default=0.0, constraints=[SQL('comment \'泊入/泊出成功率\'')])
    rate_cruise = FloatField(default=0.0, constraints=[SQL('comment \'巡航成功率\'')])
    rate_calculate_road = FloatField(default=0.0, constraints=[SQL('comment \'算路成功率\'')])

    class Meta:
        table_name = 'vpd_test_stats'


class VpdTestRoute(BaseModel):
    parking_lot = CharField(constraints=[SQL('comment \'停车场\'')], max_length=255)
    username = CharField(constraints=[SQL('comment \'创建人\'')], max_length=50)
    route_info = JSONTextField(default=list, constraints=[SQL('comment \'路线信息\'')])
    route_name = CharField(constraints=[SQL('comment \'路线名称\'')])

    class Meta:
        table_name = 'vpd_test_route'


class VpdTestEventStats(BaseModel):
    task_id = ForeignKeyField(VpdTestTask)

    vin_code = CharField(constraints=[SQL('comment \'车架号\'')], max_length=50)
    vehicle_name = CharField(constraints=[SQL('comment \'车辆名称\'')], max_length=50, default='')
    app_name = CharField(constraints=[SQL('comment \'app名称\'')], max_length=50)
    app_version = CharField(constraints=[SQL('comment \'app版本\'')], max_length=50)
    ads_version = CharField(constraints=[SQL('comment \'ads版本\'')], max_length=255, default='')
    device_type = CharField(constraints=[SQL('comment \'设备类型\'')], max_length=20)

    event_time = DateTimeField(constraints=[SQL('comment \'事件时间\'')])
    event_classify = CharField(constraints=[SQL('comment \'事件分类\'')])
    event_describe = CharField(constraints=[SQL('comment \'事件描述\'')])
    ddi_link = CharField(default='', constraints=[SQL('comment \'ddi链接\'')])

    event_type = SmallIntegerField(default=1, constraints=[SQL('comment \'事件类型\'')])

    class Meta:
        table_name = 'vpd_test_event_stats'


class VpdTestCaseStats(BaseModel):
    task_id = ForeignKeyField(VpdTestTask)
    case_type = IntegerField(constraints=[SQL('comment \'用例类型\'')], default=0)
    sub_case_type = IntegerField(constraints=[SQL('comment \'子用例类型\'')], default=0)

    vin_code = CharField(constraints=[SQL('comment \'车架号\'')], max_length=50)
    vehicle_name = CharField(constraints=[SQL('comment \'车辆名称\'')], max_length=50, default='')
    app_name = CharField(constraints=[SQL('comment \'app名称\'')], max_length=50)
    app_version = CharField(constraints=[SQL('comment \'app版本\'')], max_length=50)
    ads_version = CharField(constraints=[SQL('comment \'ads版本\'')], max_length=255, default='')
    device_type = CharField(constraints=[SQL('comment \'设备类型\'')], max_length=20)
    device_sn = CharField(constraints=[SQL('comment \'设备SN\'')], max_length=20)
    device_name = CharField(constraints=[SQL('comment \'设备名称\'')], max_length=20)

    result = CharField(constraints=[SQL('comment \'用例执行结果\'')], max_length=20)
    t1 = DateTimeField(constraints=[SQL('comment \'算路开始时间\'')])
    t2 = DateTimeField(constraints=[SQL('comment \'视频加载成功时间\'')])
    t3 = DateTimeField(constraints=[SQL('comment \'辅助驾驶条件不满足时间\'')])
    t4 = DateTimeField(constraints=[SQL('comment \'算路成功时间\'')])
    t5 = DateTimeField(constraints=[SQL('comment \'路径规划失败时间\'')])
    power_on_time = FloatField(constraints=[SQL('comment \'上电过程时长\'')])
    remark = CharField(constraints=[SQL('comment \'备注\'')])
    epoch = IntegerField(constraints=[SQL('comment \'轮次\'')])

    class Meta:
        table_name = 'vpd_test_case_stats'

    @classmethod
    def new(cls, task_id: int, case_type: int, vin_code: str, vehicle_name: str, app_name: str, app_version: str,
            ads_version: str, device_type: str, result: str, t1=None, t2=None, t3=None, t4=None, t5=None,
            power_on_time=None, remark=None, sub_case_type: int = None, device_sn: str = None, device_name: str = None,
            epoch: int = None):
        db_stats = {
            'task_id': task_id,
            'case_type': case_type,
            'sub_case_type': sub_case_type,
            'vin_code': vin_code,
            'vehicle_name': vehicle_name,
            'app_name': app_name,
            'app_version': app_version,
            'ads_version': ads_version,
            'device_type': device_type,
            'device_sn': device_sn,
            'device_name': device_name,
            'result': result,
            't1': t1,
            't2': t2,
            't3': t3,
            't4': t4,
            't5': t5,
            'power_on_time': power_on_time,
            'remark': remark,
            'epoch': epoch,
        }
        return cls.gateway().insert(db_stats)


class VpdTestUpgradeTask(BaseModel):
    STATUS_MAP = {
        0: '待运行',
        1: '执行中',
        2: '已完成',
        3: '异常中断'
    }
    username = CharField(constraints=[SQL('comment \'创建人\'')], max_length=50)
    executor_ip = CharField(constraints=[SQL('comment \'执行机ip\'')], max_length=20)
    device_list = JSONTextField(default=list, constraints=[SQL('comment \'设备列表\'')])
    android_pkg_info = JSONTextField(default=list, constraints=[SQL('comment \'安卓包信息\'')])
    harmony_pkg_info = JSONTextField(default=list, constraints=[SQL('comment \'鸿蒙包信息\'')])
    result = JSONTextField(default=list, constraints=[SQL('comment \'升级结果\'')])
    status = SmallIntegerField(default=1, constraints=[SQL('comment \'任务状态 0未启动 1运行中')])

    class Meta:
        table_name = 'vpd_test_upgrade_task'