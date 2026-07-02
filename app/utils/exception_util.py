
class AppTypeKeyError(Exception):
    """app类型错误"""
    def __init__(self, info: str):
        super().__init__(f'Unsupported app type, info: {info}')


class DeviceTypeKeyError(Exception):
    """设备类型错误"""
    def __init__(self, info: str):
        super().__init__(f'Unsupported device type, info: {info}')


class AppLaunchException(Exception):
    """app启动异常"""
    def __init__(self):
        super().__init__('The app fails to launch.')


class ManualStopEvent(Exception):
    """手动停止"""
    def __init__(self):
        super().__init__('Task stop event.')


class StopEvent(Exception):
    """停止"""
    def __init__(self):
        super().__init__('Task stop event.')


class NoMapException(Exception):
    """不进图"""
    def __init__(self):
        super().__init__('classify: 进图异常; describe: 进图异常')


class UnavailableException(Exception):
    """暂不可用"""
    def __init__(self, message: str):
        new_message = f'classify: 暂不可用; describe: {message}'
        super().__init__(new_message)


class ExitedException(Exception):
    """异常退出"""
    def __init__(self, message: str):
        new_message = f'classify: 掉自驾溜车; describe: {message}'
        super().__init__(new_message)


class PlanningFailureException(Exception):
    """算路失败"""
    def __init__(self, message: str):
        new_message = f'classify: 算路失败; describe: {message}'
        super().__init__(new_message)


class ParkingTimeoutException(Exception):
    """临停超时"""
    def __init__(self):
        super().__init__('classify: 临停效率; describe: 临停超时50s')


class RiskTimeoutException(Exception):
    """脱困超时"""
    def __init__(self):
        super().__init__('classify: 脱困效率; describe: 脱困超时50s')


class ParkingToggleException(Exception):
    """临停-巡航来回切换"""
    def __init__(self):
        super().__init__('classify: 临停-巡航来回切换; describe: 60s内临停和巡航有多次切换')


class RiskToggleException(Exception):
    """脱困-巡航来回切换"""
    def __init__(self):
        super().__init__('classify: 脱困-巡航来回切换; describe: 60s内脱困和巡航有多次切换')


class ParkingInTimeoutException(Exception):
    """泊入超时"""
    def __init__(self):
        super().__init__('classify: 泊入效率; describe: 泊入超过50s')


class ParkingOutTimeoutException(Exception):
    """泊出超时"""
    def __init__(self):
        super().__init__('classify: 泊出效率; describe: 泊出超过50s')


class HeavyBrakeEvent(Exception):
    """重刹"""
    def __init__(self):
        super().__init__('classify: 重刹; describe: 速度短时间下降5km/s')


class AebEvent(Exception):
    """触发AEB"""
    def __init__(self):
        super().__init__('classify: 触发AEB; describe: 巡航过程中触发AEB进暂停模式')


class AebBlockEvent(Exception):
    """AEB卡死"""
    def __init__(self):
        super().__init__('classify: 触发AEB; describe: 60s内多次出现AEB')


class VehicleBlockEvent(Exception):
    """车辆卡死"""
    def __init__(self):
        super().__init__('classify: 车辆卡死; describe: 车辆无响应')


class CruiseBlockEvent(Exception):
    """巡航卡死"""
    def __init__(self):
        super().__init__('classify: 巡航卡死; describe: 页面卡住240s-车速为0')


class StartCruiseError(Exception):
    """开始巡航错误"""
    def __init__(self):
        super().__init__('classify: 巡航开始异常; describe: 巡航开始异常')


class ManualTakeoverEvent(Exception):
    """人工接管"""
    def __init__(self):
        super().__init__('需人工接管')


class ParkingSpaceException(Exception):
    """无可用匹配车位"""
    def __init__(self):
        super().__init__('无可用匹配车位')


class CruiseTriggerEvent(Exception):
    """巡航卡死"""
    def __init__(self):
        super().__init__('classify: 巡航卡死; describe: 页面卡住30s-车速为0')


class CruiseRecordEvent(Exception):
    """巡航卡死"""
    def __init__(self):
        super().__init__('classify: 巡航卡死; describe: 页面卡住50s-车速为0')


class CruiseWalkEvent(Exception):
    """巡航转漫游"""
    def __init__(self):
        super().__init__('classify: 车位被占转漫游; describe: 未泊入目标车位')


class ChargeError(Exception):
    """代客充电异常"""
    def __init__(self, info: str):
        super().__init__(f'classify: 代客充电异常; describe: {info}')


class ChargeErrorBackEvent(Exception):
    """代客充电异常"""
    def __init__(self):
        super().__init__(f'classify: 代客充电异常; describe: 即将返回默认车位')


class SentinelException(Exception):
    """哨兵模式异常"""
    def __init__(self, info: str):
        super().__init__(f'classify: 哨兵模式异常; describe: {info}')