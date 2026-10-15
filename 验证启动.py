# -*- coding: utf-8 -*-
"""GUI 启动验证脚本：复刻 main.py 的启动路径，5 秒后优雅关闭。

用途：验证 WebView2 窗口能正常弹出、JS 桥注入无递归崩溃、
     关闭窗口后进程干净退出（无 1411 等 WebView2 报错）。
用法：.venv/Scripts/python.exe 验证启动.py
"""

import sys
import threading
import time

import webview

from main import APP_TITLE, UI_PATH, Api, _setup_drop


def main():
    api = Api()
    window = webview.create_window(
        APP_TITLE,
        url=UI_PATH,
        js_api=api,
        width=1080,
        height=780,
        min_size=(900, 640),
    )
    api._window = window
    # 与 main.py 相同：loaded 事件后注册拖拽
    window.events.loaded += lambda: _setup_drop(window)

    # 验证 JS 桥真实可用：pywebview 注入成功后调用 get_state
    def check_bridge():
        try:
            api.get_state()  # Python 侧直调
            js_ok = window.evaluate_js("typeof window.pywebview.api")
            print("BRIDGE:", js_ok)
        except Exception as e:
            print("BRIDGE_FAIL:", e)

    window.events.loaded += check_bridge

    # 5 秒后优雅关闭（正常关窗路径，不用 taskkill）
    def auto_close():
        time.sleep(5)
        window.destroy()

    threading.Thread(target=auto_close, daemon=True).start()
    webview.start()
    print("START_OK: 窗口弹出且优雅退出")


if __name__ == "__main__":
    main()
