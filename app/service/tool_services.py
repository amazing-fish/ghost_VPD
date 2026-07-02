import os
import json
import time
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, ClassVar
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from app.utils.config_util import config_util

class TaskStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    ERROR = "error"
    NO_TASK = "no_task"
    CHECK_PASSED = "check_passed"
    SKIPPED = "skipped"
    CHECK_FAILED = "check_failed"
    ABORTED = "aborted"

@dataclass()
class Device:
    device_sn: str
    device_type: str

@dataclass()
class AppResult:
    app: str
    version: str
    status: TaskStatus
    log: str = ""

@dataclass()
class DeviceUpgradeResult:
    device_sn: str
    status: TaskStatus
    success_count: int
    total_count: int
    apps: List[AppResult]
    reason: Optional[str] = None

class AppUpgradeTool:
    TASK_TIMEOUT: ClassVar[int] = 180
    RETRY_COUNT: ClassVar[int] = 1
    ANDROID_MAX_WORKERS: ClassVar[int] = 6
    HARMONY_MAX_WORKERS: ClassVar[int] = 3

    RECORD_FILE: ClassVar[Path] = Path("upgrade_record.json")

    PACKAGE_MAP: ClassVar[Dict[str, Dict[str, str]]] = {
        "#01": {"android": "app.huawei.motor", "harmony": "app.huawei.hmos.motor"},
        "#02": {"android": "com.avatar.buyer.client"},
        "#03": {"android": "com.voyah.fassistant"},
        "#04": {"android": "com.cloudy.component"},
        "#05": {"android": "deepal.com.app"},
        "#06": {"android": "com.byd.aeri.caranywhere"},
        "#07": {"harmony": "adsmobilesdk.aito.huawei"},
        "#08": {"android": "com.yinwang.qiankunapp", "harmony": "com.yinwang.qiankunapp.hm"},
    }

    def __init__(self, config_path: str = "config.json") -> None:
        self._config_path = Path(config_path)
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._records_cache: Optional[List[dict]] = None
        self._app_dir = self._init_and_validate_app_dir()

    def _init_and_validate_app_dir(self) -> Path:
        app_dir_str = config_util.config.get('app_dir', '')
        if not isinstance(app_dir_str, str) or not app_dir_str:
            raise

        app_dir = Path(app_dir_str).resolve()
        if not app_dir.exists():
            raise NotADirectoryError(f"无效目录:{app_dir}")
        return app_dir

    def _get_cmd_prefix(self, device_type: str, sn: str) -> List[str]:
        return ["adb", "-s", sn] if device_type == "android" else ["hdc", "-t", sn]

    def _exec_cmd(self, cmd: List[str], timeout: int = 60) -> Tuple[bool, str]:
        run_kw = {
            "shell": False,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace"
        }
        if os.name == "nt":
            run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            if self._stop_event.is_set():
                return False, "aborted"

            process = subprocess.Popen(cmd, **run_kw)
            start_time = time.time()

            while process.poll() is None:
                if self._stop_event.is_set():
                    process.terminate()
                    process.wait(timeout=2)
                    return False, "aborted"
                if time.time() - start_time > timeout:
                    process.kill()
                    return False, "timeout"
                time.sleep(0.1)

            stdout, stderr = process.communicate()
            return process.returncode == 0, (stdout + stderr).strip()

        except (subprocess.SubprocessError, OSError) as e:
            return False, f"error: {str(e)}"

    def _get_package_name(self, app: str, device_type: str) -> Optional[str]:
        return self.PACKAGE_MAP.get(app, {}).get(device_type)

    def _load_records(self) -> List[dict]:
        if not self.RECORD_FILE.exists():
            return []
        try:
            with self.RECORD_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            return []

    def _get_cached_records(self) -> List[dict]:
        with self._cache_lock:
            if self._records_cache is None:
                self._records_cache = self._load_records()
            return self._records_cache.copy()

    def _write_record(self, record: dict) -> None:
        with self._write_lock:
            try:
                records = self._load_records()
                records.append(record)
                with self.RECORD_FILE.open("w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
                with self._cache_lock:
                    self._records_cache = records
            except (IOError, OSError, json.JSONDecodeError):
                pass

    def _get_last_install_record(self, sn: str, app: str) -> Optional[dict]:
        records = self._get_cached_records()
        matched = [r for r in records if r.get("sn") == sn and r.get("app") == app]
        return matched[-1] if matched else None

    def _is_device_ready(self, sn: str, device_type: str) -> bool:
        if device_type == "android":
            ok, out = self._exec_cmd(["adb", "devices", "-l"])
            if not ok:
                return False
            for line in out.strip().splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == sn and parts[1] == "device":
                    return True
            return False

        ok, out = self._exec_cmd(["hdc", "list", "targets"])
        return ok and sn in out

    def _is_app_installed(self, sn: str, device_type: str, app: str) -> bool:
        """判断app是否下载"""
        pkg = self._get_package_name(app, device_type)
        if not pkg or pkg.endswith(".apk"):
            return False

        if device_type == "android":
            cmd = self._get_cmd_prefix(device_type, sn) + ["shell", "pm", "path", pkg]
            return self._exec_cmd(cmd, timeout=8)[0]

        cmd = self._get_cmd_prefix(device_type, sn) + ["shell", "bm", "dump", "-n", pkg]
        ok, out = self._exec_cmd(cmd, timeout=8)
        out = out.strip()
        if not out or "error" in out or "failed" in out:
            return False

        return True

    def _uninstall_app(self, sn: str, device_type: str, app: str) -> bool:
        """卸载app"""
        try:
            pkg = self._get_package_name(app, device_type)
            if not pkg:
                return True
            cmd = self._get_cmd_prefix(device_type, sn) + ["uninstall", pkg]
            return self._exec_cmd(cmd, timeout=15)[0]
        except Exception:
            return False

    def _process_single_app_install(self, sn: str, device_type: str, app: str, pkg_file: str, pkg_path: Path
    ) -> AppResult:
        """单一软件下载"""
        real_installed = self._is_app_installed(sn, device_type, app)
        last_record = self._get_last_install_record(sn, app)

        if real_installed and last_record and last_record.get("pkg") == pkg_file:
            return AppResult(app, pkg_file, TaskStatus.SKIPPED, "latest package already installed")

        ok_flag, log = False, ""

        if real_installed:
            if not self._uninstall_app(sn, device_type, app):
                return AppResult(app, pkg_file, TaskStatus.FAILED, "uninstall failed")

        for _ in range(self.RETRY_COUNT):
            if self._stop_event.is_set():
                log = "aborted"
                break

            if device_type == "android":
                ok, log = self._exec_cmd(["adb", "-s", sn, "install", "-r", str(pkg_path)])
                ok_flag = self._is_app_installed(sn, device_type, app)
                break
            else:
                ok, log = self._exec_cmd(["hdc", "-t", sn, "install", str(pkg_path)])
                ok_flag = self._is_app_installed(sn, device_type, app)
                if not ok_flag:
                    log = "install failed after check"
                break

        self._write_record({
            "sn": sn, "app": app, "pkg": pkg_file,
            "status": "success" if ok_flag else "failed",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        return AppResult(app, pkg_file, TaskStatus.SUCCESS if ok_flag else TaskStatus.FAILED, log)

    def _upgrade_single(self, device: Device, strategy: Dict[str, Dict[str, str]], dry_run: bool) -> DeviceUpgradeResult:
        """单一软件升级"""
        sn, typ = device.device_sn, device.device_type

        if not sn or not typ:
            return DeviceUpgradeResult(sn, TaskStatus.ERROR, 0, 0, [], "invalid info")
        if dry_run:
            if not self._is_device_ready(sn, typ):
                return DeviceUpgradeResult(sn, TaskStatus.OFFLINE, 0, 0, [], "offline")
            for app, sm in strategy.items():
                pkg_file = sm.get(typ)
                if not pkg_file:
                    continue
                pkg_path = self._app_dir / app / typ / pkg_file
                if not pkg_path.exists():
                    return DeviceUpgradeResult(sn, TaskStatus.CHECK_FAILED, 0, 0, [], f"包不存在:{pkg_path}")
            return DeviceUpgradeResult(sn, TaskStatus.CHECK_PASSED, 0, 0, [])

        if not self._is_device_ready(sn, typ):
            return DeviceUpgradeResult(sn, TaskStatus.OFFLINE, 0, 0, [], "offline")

        apps = []
        for app, sm in strategy.items():
            pkg_file = sm.get(typ)
            if not pkg_file:
                continue

            pkg_path = self._app_dir / app / typ / pkg_file
            if not pkg_path.exists():
                apps.append(AppResult(app, pkg_file, TaskStatus.SKIPPED, "package not found"))
                continue

            app_result = self._process_single_app_install(sn, typ, app, pkg_file, pkg_path)
            apps.append(app_result)

        total = len(apps)
        succ = sum(1 for a in apps if a.status == TaskStatus.SUCCESS)
        if total == 0:
            status = TaskStatus.NO_TASK
        elif succ == total:
            status = TaskStatus.SUCCESS
        elif succ > 0:
            status = TaskStatus.PARTIAL_SUCCESS
        else:
            status = TaskStatus.FAILED
        return DeviceUpgradeResult(sn, status, succ, total, apps)

    def _batch_run(self, devices: List[Device], strategy: Dict[str, Dict[str, str]], dry_run: bool) -> List[DeviceUpgradeResult]:
        if not devices:
            return []
        dev_type = devices[0].device_type
        max_workers = self.ANDROID_MAX_WORKERS if dev_type == "android" else self.HARMONY_MAX_WORKERS
        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {pool.submit(self._upgrade_single, d, strategy, dry_run): d for d in devices}
            for future, dev in future_map.items():
                try:
                    results.append(future.result(timeout=self.TASK_TIMEOUT))
                except TimeoutError:
                    results.append(DeviceUpgradeResult(dev.device_sn, TaskStatus.TIMEOUT, 0, 0, [], "timeout"))
                except Exception as e:
                    results.append(DeviceUpgradeResult(dev.device_sn, TaskStatus.ERROR, 0, 0, [], str(e)))
        return results

    def batch_upgrade(self, device_list: List[Device], strategy: Dict[str, Dict[str, str]], dry_run=False) -> Dict[str, Any]:
        self._stop_event.clear()
        android_dev = [d for d in device_list if d.device_type == "android"]
        harmony_dev = [d for d in device_list if d.device_type == "harmony"]
        results = self._batch_run(android_dev, strategy, dry_run) + self._batch_run(harmony_dev, strategy, dry_run)
        total = len(results)
        success_count = sum(1 for r in results if r.status in {TaskStatus.SUCCESS, TaskStatus.PARTIAL_SUCCESS, TaskStatus.SKIPPED})
        return {
            "total": total,
            "success_count": success_count,
            "failed_count": total - success_count,
            "results": results
        }

    def stop_all(self) -> None:
        self._stop_event.set()

if __name__ == "__main__":
    try:
        tool = AppUpgradeTool()
    except Exception as e:
        print(f"初始化失败: {e}")
        exit()

    test_devices = [Device(device_sn="4NZ0225701005219", device_type="harmony")]
    upgrade_strategy = {
        "#01": {
            "android": "华为乾崑v1.0.18.200.1-pre.apk",
            "harmony": "Harmony-HIMA-Pre-release-3.0.1.280.hap"
        }
    }
    result = tool.batch_upgrade(test_devices, upgrade_strategy, dry_run=False)
    print(result)