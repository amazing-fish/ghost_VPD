"""本机控制台：鸿蒙 NEXT 投屏 + 手动操作 + 用例/用例集进度（vpdtest console）。

控制台是给人看的工具，不是执行器：Agent 的设备动作仍然只能通过 `vpdtest act/run/explore`
（受 policy、授权与证据约束）。控制台只监听 127.0.0.1，带随机 token；帧只在内存中。
"""
