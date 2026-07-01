"""하드닝 배치 4 (마지막) — 중첩깊이가드 / store키검증 / 목록캡 / 권한감사.

실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_hardening4.py
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

    import v.boards.whiteboard.cmd_data as cd
    dm = {}
    cd.load_data = lambda: dict(dm)
    cd.save_data = lambda d: (dm.clear(), dm.update(d))
    import v.boards.whiteboard.cmd_functions as cf
    fm = {}
    cf.load_functions = lambda: dict(fm)
    cf.save_functions = lambda d: (fm.clear(), fm.update(d))

    from v.app import App
    from v.boards.whiteboard.plugin import WhiteBoardPlugin
    from v.boards.whiteboard.chat_commands import ChatCommandController
    plugin = WhiteBoardPlugin(App())
    plugin.create_view()

    class UI:
        current_plugin = plugin
    ctrl = ChatCommandController(UI())

    def ex(cmd):
        return ctrl.execute(cmd)

    def val(cmd):
        return ex(cmd).value

    print("1) 중첩 깊이 가드:")
    # 자기 자신을 부르는 함수 → 깊이 상한에서 멈춤(크래시 없이)
    val("function set loop function loop")
    r = ex("function loop")
    check("recursive function stops", r is not None and hasattr(r, "ok"))
    check("exec_depth reset after run", ctrl._exec_depth == 0)

    print("2) execute store 키 검증:")
    check("store empty key rejected", val("execute store result data  run say x") == 0
          or val('execute store result data "" run say x') == 0)
    check("store dot key rejected", val("execute store result data a.b run grep x") == 0)
    check("store valid ok", val("execute store result data cnt run grep zzz없음") in (0, 1)
          and "cnt" in cd.load_data())

    print("3) 목록 출력 캡:")
    for i in range(45):
        cd.data_set(f"k{i}", i)
    r = ex("data list")
    shown = [m for m in r.messages if m.strip().startswith("§b" + "" ) or "= " in m]
    check("data list capped ~30", any("외 " in m and "개" in m for m in r.messages))
    for i in range(40):
        cf.set_function(f"fn{i}", "say x")
    check("function list capped", any("외 " in m for m in ex("function list").messages))

    print("4) 권한 감사 (Visitor 는 모든 쓰기 차단, 읽기 허용):")
    class Visitor:
        level = 0
        is_connected = True
        username = "guest"
    ctrl.set_client(Visitor())

    base = len(plugin.app.nodes)
    writes = [
        "create chat{name:z}", "delete z", "connect a b", "run z", "move z 0 0",
        "data set w 1", "data merge w {x:1}", "data remove hp",
        "function set w say x", "function add w say y", "function remove loop",
        "execute if node z run say x",
    ]
    blocked = all(ctrl.execute(c).value == 0 for c in writes)
    check("all write commands blocked for visitor", blocked)
    check("no node created by visitor", len(plugin.app.nodes) == base)
    reads_ok = (ex("grep x").ok and ex("data list").ok and ex("function list").ok
                and ex("data get hp").ok and ex("help").ok)
    check("read commands allowed for visitor", reads_ok)
    ctrl.set_client(None)

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
