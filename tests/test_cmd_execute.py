"""`/data` (자체 DB) + `/execute` (조건부/데이터 실행) 회귀 테스트.

저장소 인메모리 패치. 라이브 플러그인 offscreen.
실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_execute.py
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
    app = QApplication.instance() or QApplication([])  # ref 유지

    from v.boards.whiteboard import cmd_execute as ce
    from v.boards.whiteboard.cmd_params import parse_value

    print("helpers:")
    check("range single", ce.parse_range("10") == (10.0, 10.0))
    check("range lo..", ce.parse_range("10..") == (10.0, None))
    check("range ..hi", ce.parse_range("..10") == (None, 10.0))
    check("range lo..hi", ce.parse_range("5..10") == (5.0, 10.0))
    check("match in", ce.match_range(7, (5.0, 10.0)))
    check("match out", not ce.match_range(3, (5.0, 10.0)))
    check("compare gt", ce.compare(5, ">", 3) and not ce.compare(2, ">", 3))
    check("tokenize quotes", ce.tokenize('a "b c" d') == ["a", "b c", "d"])
    check("tokenize brace", ce.tokenize("say {x:1, y:2}") == ["say", "{x:1, y:2}"])
    check("parse_value num", parse_value("20") == 20)
    check("parse_value compound", parse_value("{x:1}") == {"x": 1})

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

    def val(cmd):
        return ctrl.execute(cmd).value

    print("/data:")
    check("set", val("data set hp 20") == 1)
    check("set compound", val("data set pos {x:100, y:64}") == 1)
    check("get", val("data get hp") == 20)
    check("get path", val("data get pos x") == 100)
    check("get missing", val("data get nope") == 0)
    check("list", val("data list") == 1)
    check("remove", val("data remove hp") == 1)
    check("remove gone", val("data get hp") == 0)
    val("data set hp 20")

    print("/execute conditions:")
    check("if data matches pass", val("execute if data hp matches 10.. run say x") == 1)
    check("if data matches fail", val("execute if data hp matches 50.. run say x") == 0)
    check("unless negate", val("execute unless data hp matches 50.. run say x") == 1)
    check("if data cmp", val("execute if data hp > 15 run say x") == 1)
    val('create number{value:120, name:Counter}')
    check("if value pass", val("execute if value Counter matches 100.. run say x") == 1)
    check("if value fail", val("execute if value Counter < 100 run say x") == 0)
    check("if node exists", val("execute if node Counter run say x") == 1)
    check("unless node absent", val("execute unless node Ghost run say x") == 1)

    print("/execute chain + store + test-mode:")
    check("chain AND pass", val("execute if data hp matches 10.. if value Counter matches 100.. run say x") == 1)
    check("chain AND fail", val("execute if data hp matches 10.. if value Counter matches 999.. run say x") == 0)
    check("test-mode pass", val("execute if data hp matches 10..") == 1)
    check("test-mode fail", val("execute if data hp matches 99..") == 0)
    ctrl.execute("execute store result data cnt run grep Counter")
    check("store result", cd.load_data().get("cnt") == 1)

    print("/execute errors:")
    check("unknown condition", val("execute if bogus foo run say x") == 0)
    check("unknown keyword", val("execute hello") == 0)

    print("function 연동:")
    val("function set guard execute if value Counter matches 100.. run delete Counter")
    check("function+execute", val("function guard") == 1)
    check("node deleted by guard", not any(plugin.get_node_name(n) == "Counter" for n in plugin.app.nodes))

    print("autocomplete:")
    sug = lambda s: [c.text for c in ctrl.suggest(s, len(s))]
    check("data subcommands", "set" in sug("data "))
    check("data key suggest", "cnt" in sug("data get c"))

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
