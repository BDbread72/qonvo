"""명령 시스템 하드닝 배치 1 — 깊이가드/매크로이스케이프/크기한도/권한.

실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_hardening.py
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

    from v.boards.whiteboard.cmd_params import parse_tag, TagSyntaxError
    from v.boards.whiteboard.cmd_functions import apply_macro

    print("1) 깊이 가드 (크래시 방지):")
    def deep_ok(s):
        try:
            parse_tag(s); return False           # 오류 없으면 실패
        except TagSyntaxError:
            return True
        except RecursionError:
            return False                          # 크래시 = 실패
    check("deep compound → error not crash", deep_ok("{a:" * 100 + "1" + "}" * 100))
    check("deep list → error not crash", deep_ok("{x:" + "[" * 100 + "1" + "]" * 100 + "}"))
    check("normal nesting still works", parse_tag("{a:{b:1}}") == {"a": {"b": 1}})
    check("list of compounds", parse_tag("{i:[{a:1},{b:2}]}") == {"i": [{"a": 1}, {"b": 2}]})

    print("2) 매크로 값 이스케이프:")
    # 따옴표/역슬래시가 든 값이 큰따옴표 컨텍스트를 안 깨고 다시 파싱되어야
    line = apply_macro('create text{text:"$(m)"}', {"m": 'a"b\\c'})
    check("escaped line reparses", parse_tag(line[line.index("{"):]) == {"text": 'a"b\\c'})

    print("3) 크기 한도:")
    import v.boards.whiteboard.cmd_data as cd
    dm = {}
    cd.load_data = lambda: dict(dm)
    cd.save_data = lambda d: (dm.clear(), dm.update(d))
    import v.boards.whiteboard.cmd_functions as cf
    fm = {}
    cf.load_functions = lambda: dict(fm)
    cf.save_functions = lambda d: (fm.clear(), fm.update(d))

    def raises_v(fn, *a):
        try:
            fn(*a); return False
        except ValueError:
            return True
    check("oversized data value rejected", raises_v(cd.data_set, "big", "x" * 70000))
    check("oversized function body rejected", raises_v(cf.set_function, "big", "y" * 70000))
    check("normal data ok", (cd.data_set("hp", 20) or True) and cd.load_data()["hp"] == 20)

    print("4) 권한 — Visitor 는 /function·/execute 로도 우회 불가:")
    from v.app import App
    from v.boards.whiteboard.plugin import WhiteBoardPlugin
    from v.boards.whiteboard.chat_commands import ChatCommandController
    plugin = WhiteBoardPlugin(App())
    plugin.create_view()

    class UI:
        current_plugin = plugin
    ctrl = ChatCommandController(UI())

    # 솔로(=Operator)로 위험한 줄이 든 함수 정의
    ctrl.execute("function set danger create chat{name:해킹}")

    class Visitor:          # 서버 Visitor(레벨0) 흉내
        level = 0
        is_connected = True
        username = "guest"
    ctrl.set_client(Visitor())

    def n_count():
        return len(plugin.app.nodes)

    base = n_count()
    ctrl.execute("create chat{name:x}")          # 직접 차단
    check("visitor cannot /create", n_count() == base)
    ctrl.execute("function danger")              # 함수로도 차단(내부 줄이 권한 재검사)
    check("visitor cannot escalate via /function", n_count() == base)
    check("visitor cannot /execute", ctrl.execute("execute if node x run create chat{name:y}").value == 0
          and n_count() == base)
    # 읽기 전용은 Visitor 도 됨
    check("visitor can /grep", ctrl.execute("grep x").ok)
    ctrl.set_client(None)

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
