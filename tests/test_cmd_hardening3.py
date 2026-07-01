"""하드닝 배치 3 — run all 가드 / 출력 폭주 캡 / grep 텍스트 캡 / 입력 스윕.

실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_hardening3.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_fail = 0


def check(name, cond):
    global _fail
    if not cond:
        _fail += 1
    print(f"  [{'OK ' if cond else 'XX '}] {name}")


def main():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from v.boards.whiteboard import chat_commands as cc
    from v.boards.whiteboard.chat_commands import _node_search_text, _GREP_TEXT_CAP

    print("3) grep 텍스트 캡:")
    class FakeNode:
        _history = [{"user": "u" * 100000, "response": "r" * 100000}]
    txt = _node_search_text(_FakePlugin(), 1, FakeNode())
    check("per-node text capped", len(txt) <= _GREP_TEXT_CAP + 200)

    print("2) 출력 폭주 캡 (ChatSource):")
    from v.boards.whiteboard.chat_commands import ChatSource, CommandContext
    src = ChatSource(CommandContext(_UI(None)))
    out = []
    src.set_output(out.append)
    for i in range(200):
        src.send_message(f"line {i}")
    check("output capped ~60", len(out) <= ChatSource._MAX_MSGS + 2)
    check("truncation notice", any("생략" in m for m in out))

    import v.boards.whiteboard.cmd_data as cd
    dm = {}
    cd.load_data = lambda: dict(dm)
    cd.save_data = lambda d: (dm.clear(), dm.update(d))

    from v.app import App
    from v.boards.whiteboard.plugin import WhiteBoardPlugin
    from v.boards.whiteboard.chat_commands import ChatCommandController
    plugin = WhiteBoardPlugin(App())
    plugin.create_view()

    class UI:
        current_plugin = plugin
    ctrl = ChatCommandController(UI())

    def val(cmd):
        return ctrl.execute(cmd).value

    print("1) /run all 가드:")
    for i in range(25):
        ctrl.execute(f"create chat{{name:C{i}}}")
    r = ctrl.execute("run all")
    check("run all succeeds", r.value == 1)
    check("run all cap message", any("20개까지만" in m or "비용" in m for m in r.messages))

    print("4) 입력 스윕:")
    check("data get empty rejected", val('data get ""') == 0)
    check("data merge dot rejected", val('data merge a.b {x:1}') == 0)
    check("data get missing key clean", val("data get 없는키") == 0)
    check("normal data still ok", val("data set hp 20") == 1 and val("data get hp") == 20)

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


class _FakePlugin:
    def get_node_name(self, nid):
        return "n"


class _UI:
    def __init__(self, plugin):
        self.current_plugin = plugin


if __name__ == "__main__":
    main()
