"""`/function` (마크식 명령 매크로 + 매크로 변수 + return) 회귀 테스트.

저장소는 인메모리로 패치(실제 settings 오염 방지). 라이브 플러그인 offscreen.
실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_functions.py
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

    import v.boards.whiteboard.cmd_functions as cf
    mem = {}
    cf.load_functions = lambda: dict(mem)
    cf.save_functions = lambda d: (mem.clear(), mem.update(d))

    print("helpers:")
    check("split newline+semicolon",
          cf.split_commands("a\nb; c") == ["a", "b", "c"])
    check("split respects braces",
          cf.split_commands("create x{a:1; b:2}; y") == ["create x{a:1; b:2}", "y"])
    check("split drops comments", cf.split_commands("# note\nreal") == ["real"])
    check("macro keys", cf.macro_keys("$say $(a) $(b)\nplain $(c)") == ["a", "b"])
    check("apply macro", cf.apply_macro('say $(x)!', {"x": "hi"}) == "say hi!")

    def raises(fn):
        try:
            fn(); return False
        except ValueError:
            return True
    check("macro missing key", raises(lambda: cf.apply_macro("$(z)", {})))

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

    def names():
        return {plugin.get_node_name(nid) for nid in plugin.app.nodes}

    print("define/show/list:")
    check("set", val('function set setup create prompt{name:"Sys"}; create chat{name:"Bot"}; connect Sys Bot') == 1)
    check("stored", "setup" in cf.list_functions())
    check("list", val('function list') == 1)
    check("show", val('function show setup') == 1)
    check("reserved name blocked", val('function set list foo') == 0)
    check("add line", val('function add setup say done') == 1)

    print("run:")
    check("run setup", val('function setup') == 1)
    check("nodes created", {"Sys", "Bot"} <= names())
    check("edge created", len(plugin._edges) == 1)
    check("run missing fails", val('function nope') == 0)

    print("macros:")
    check("set macro", val('function set greet $create text{text:"hi $(who)"}') == 1)
    check("run with args", val('function greet {who:"World"}') == 1)
    check("missing arg fails", val('function greet') == 0)
    # 치환된 텍스트가 실제 노드에 들어갔는지 grep 으로 확인
    check("macro substituted", val('grep hi World') == 1)

    print("return:")
    val('function set early say one; return 5; say two')
    check("return value", ctrl.execute('function early').value == 5)
    val('function set rr return run grep Bot; say after')
    check("return run", ctrl.execute('function rr').value == 1)  # grep Bot 성공=1
    check("return outside fn fails", val('return 9') == 0)

    print("nesting + recursion guard:")
    val('function set inner return 7')
    val('function set outer function inner; say tail')
    check("nested call ok", val('function outer') == 1)         # outer 는 계속 진행
    val('function set loop function loop')
    check("recursion guarded", val('function loop') == 0)       # 폭주 없이 0

    print("autocomplete:")
    sug = lambda s: [c.text for c in ctrl.suggest(s, len(s))]
    check("subcommands", "list" in sug('function '))
    check("function names", "setup" in sug('function se'))

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
