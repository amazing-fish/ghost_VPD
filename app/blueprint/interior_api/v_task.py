from flask import g

from app.blueprint.api_helper import api_success_response
from app.blueprint.interior_api import bp_interior_api
from app.models.mysql import db
from app.service.vpd_service import batch_upgrade_packages
from app.utils.config_util import config_util


@bp_interior_api.route('/vpd/task/devices/query/', methods=['post'])
@db.connection_context()
def vpd_task_device_query():
    """
       @apiVersion 1.0.0
       @api {POST} /api/vpd/task/device/query/
       @apiName interior_vpd_task_device_query
       @apiGroup interior_v_task
    """
    return api_success_response({'devices': config_util.get_devices_info()})


@bp_interior_api.route('/vpd/package/info/query/', methods=['post'])
@db.connection_context()
def vpd_package_info_query():
    return api_success_response({'package_info': config_util.get_package_info()})


@bp_interior_api.route('/vpd/upgrade/task/execute/', methods=['post'])
@db.connection_context()
def vpd_upgrade_task_execute():
    task_id, device_list, upgrade_strategy = map(g.json.get, ['task_id', 'device_list', 'upgrade_strategy'])
    batch_upgrade_packages(task_id, device_list, upgrade_strategy)
    return api_success_response({})
