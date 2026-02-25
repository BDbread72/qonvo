"""
시스템 알림 (크로스 플랫폼)
- Windows 10/11: WinRT ToastNotification (모던 카드 스타일)
- macOS: osascript display notification
- Linux: notify-send
"""
import os
import sys
import subprocess


def _notify_windows(title: str, message: str):
    """Windows 10/11 모던 토스트 알림 (WinRT API)"""
    title_safe = title.replace("'", "''").replace('"', '`"')
    message_safe = message.replace("'", "''").replace('"', '`"')
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$nodes = $t.GetElementsByTagName('text'); "
        f"$nodes.Item(0).AppendChild($t.CreateTextNode('{title_safe}')) > $null; "
        f"$nodes.Item(1).AppendChild($t.CreateTextNode('{message_safe}')) > $null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($t); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Qonvo').Show($toast)"
    )
    try:
        subprocess.Popen(
            ['powershell', '-WindowStyle', 'Hidden', '-Command', script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _notify_macos(title: str, message: str):
    """macOS 네이티브 알림"""
    title_safe = title.replace('"', '\\"')
    message_safe = message.replace('"', '\\"')
    try:
        subprocess.Popen(
            ['osascript', '-e', f'display notification "{message_safe}" with title "{title_safe}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _notify_linux(title: str, message: str):
    """Linux 네이티브 알림 (notify-send)"""
    try:
        subprocess.Popen(
            ['notify-send', title, message, '-t', '3000'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


class ToastManager:
    """시스템 알림 관리 (Singleton)"""

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def show_toast(self, message: str, parent_window=None):
        if sys.platform == 'win32':
            _notify_windows("Qonvo", message)
        elif sys.platform == 'darwin':
            _notify_macos("Qonvo", message)
        else:
            _notify_linux("Qonvo", message)
