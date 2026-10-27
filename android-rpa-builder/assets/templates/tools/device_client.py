"""设备客户端：把设备侧的异步任务 API 包装成上层看起来同步的调用。

约定（第 2.4 / 2.12 节）：
  信封 = {"success": bool, "data": ..., "message": str}
  写操作返回 {"data": {"task_id": "..."}}，再轮 GET /api/task_result/{id}
  判据：data.success is not None 才算"有结果"，否则视为运行中
失败一律返回结构化 dict，不抛异常，便于网关区分"设备不可达"与"业务失败"。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger("rpa.device")


@dataclass
class ActionSpec:
    """超时表驱动：禁止把超时常量散落在各函数里。"""
    path: str
    timeout: int
    wait: bool
    retries: int = 2


# 逐层留余量：设备内动作 < SDK < 网关 < 调用方
ACTION_TABLE: dict[str, ActionSpec] = {
    "send_message": ActionSpec("/api/send_message", timeout=120, wait=False),
    "read_messages": ActionSpec("/api/read_messages", timeout=100, wait=True),
    "contact_list": ActionSpec("/api/get_contact_list", timeout=100, wait=True, retries=1),
    "navigate_main": ActionSpec("/api/navigate_main", timeout=40, wait=True),
}


class DeviceClient:
    def __init__(
        self,
        api_base: str,                       # http://<PHONE_IP>:<DEVICE_PORT> 或 http://127.0.0.1:<HOST_PORT>
        device_token: str = "",
        timeout: int = 120,
        poll_interval: float = 2.0,
        adb_serial: Optional[str] = None,    # 仅用于兜底取证，不用于业务执行
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.adb_serial = adb_serial
        self.session = requests.Session()
        # 关键：绕过系统代理，否则局域网直连会被本机代理劫持
        self.session.trust_env = False
        if device_token:
            self.session.headers["Authorization"] = f"Bearer {device_token}"

    # ---------- 底层 ----------

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> dict:
        url = f"{self.api_base}{path}"
        _timeout = timeout if timeout is not None else self.timeout
        _retries = retries if retries is not None else 2
        last_error: Optional[BaseException] = None
        for attempt in range(_retries):
            try:
                if method == "get":
                    resp = self.session.get(url, timeout=_timeout)
                else:
                    resp = self.session.post(url, json=data, timeout=_timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.ConnectionError as exc:
                last_error = exc
                if attempt < _retries - 1:
                    logger.warning("连接失败，重试 %s/%s: %s", attempt + 2, _retries, url)
                    time.sleep(1)
                else:
                    logger.error("无法连接到设备: %s", self.api_base)
                    return {"success": False, "message": f"无法连接到设备: {self.api_base}"}
            except (requests.Timeout, requests.ReadTimeout) as exc:
                last_error = exc
                if attempt < _retries - 1:
                    logger.warning("请求超时，重试 %s/%s: %s", attempt + 2, _retries, url)
                    time.sleep(1)
                else:
                    return {"success": False, "message": f"请求超时: {url}"}
            except requests.HTTPError as exc:
                # 409 忙/接管属于可预期业务态，透出 status 供上层判定
                status = exc.response.status_code if exc.response is not None else None
                body = {}
                try:
                    body = exc.response.json() if exc.response is not None else {}
                except ValueError:
                    pass
                return {**body, "success": False, "http_status": status,
                        "message": body.get("message") or str(exc)}
            except Exception as exc:                                  # noqa: BLE001
                return {"success": False, "message": str(exc)}
        return {"success": False, "message": str(last_error)}

    def _wait_for_result(self, task_id: str, deadline_seconds: int) -> dict:
        start = time.time()
        while time.time() - start < deadline_seconds:
            resp = self._request("get", f"/api/task_result/{task_id}", timeout=15)
            data = resp.get("data")
            if isinstance(data, dict) and data.get("success") is not None:
                return data
            time.sleep(self.poll_interval)
        return {"success": False, "message": f"任务超时 ({deadline_seconds}s): {task_id}"}

    # ---------- 业务面 ----------

    def status(self) -> dict:
        return self._request("get", "/api/status", timeout=10)

    def is_ready(self) -> bool:
        """判活：只认 httpServerRunning + accessibilityEnabled，不看 busy。"""
        resp = self.status()
        data = resp.get("data") or {}
        return bool(resp.get("success")) and bool(data.get("httpServerRunning")) and bool(data.get("accessibilityEnabled"))

    def is_busy(self) -> bool:
        return bool(self.status().get("busy"))

    def dump_ui(self) -> dict:
        """控件树只在 busy=false 时取（第 2.14 节铁律 2）。"""
        if self.is_busy():
            return {"success": False, "message": "设备忙，拒绝 dump_ui"}
        return self._request("get", "/api/dump_ui", timeout=30)

    def logs(self, limit: int = 120) -> dict:
        return self._request("get", f"/api/logs?limit={limit}", timeout=15)

    def action(self, name: str, payload: dict) -> dict:
        spec = ACTION_TABLE[name]
        resp = self._request("post", spec.path, payload, timeout=spec.timeout, retries=spec.retries)
        if not resp.get("success"):
            return resp
        task_id = (resp.get("data") or {}).get("task_id")
        if not spec.wait or not task_id:
            return resp
        return self._wait_for_result(task_id, deadline_seconds=spec.timeout)

    def set_takeover(self, enabled: bool) -> dict:
        """总闸：enabled=False 即人工接管，所有写端点会返 409。"""
        return self._request("post", "/api/config", {"enabled": enabled}, timeout=20)
