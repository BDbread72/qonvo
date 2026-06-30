"""명령 시스템 감사 수정 회귀 테스트 — 파서/식별/포트/조건 footgun 픽스.

실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_audit_fixes.py
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

    from v.boards.whiteboard.cmd_params import parse_tag, parse_value, TagSyntaxError
    from v.boards.whiteboard import cmd_functions as cf

    print("parser (cmd_params):")
    def tag_err(s):
        try:
            parse_tag(s); return False
        except TagSyntaxError:
            return True
    check("#3 missing comma errors", tag_err("{value:1 step:2}"))
    check("#3 quoted spaces ok", parse_tag('{text:"a b"}') == {"text": "a b"})
    check("#5 leading-zero kept str", parse_tag("{name:007}") == {"name": "007"})
    check("#5 sci notation kept str", parse_tag("{v:1e3}") == {"v": "1e3"})
    check("#5 plain int still int", parse_tag("{v:20}") == {"v": 20})
    check("#7 nested compound", parse_tag("{p:{x:1, c:{a:2}}}") == {"p": {"x": 1, "c": {"a": 2}}})
    check("#6 parse_value trailing errors", _raises_tag(parse_value, "1 2 3"))
    check("korean key in tag", parse_tag("{이름:중요}") == {"이름": "중요"})

    print("function split (cmd_functions):")
    # #8 멀티라인 {…} 가 줄바꿈에 안 잘림
    body = 'create text{text:"line1\nline2"}\nsay done'
    cmds = cf.split_commands(body)
    check("#8 multiline brace intact", len(cmds) == 2 and "line1\nline2" in cmds[0])

    # 라이브 플러그인
    import v.boards.whiteboard.cmd_data as cd
    dm = {}
    cd.load_data = lambda: dict(dm)
    cd.save_data = lambda d: (dm.clear(), dm.update(d))
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

    def names():
        return [plugin.get_node_name(n) for n in plugin.app.nodes]

    print("#1 korean keys/names (live):")
    check("data korean key", val("data set 점수 10") == 1 and val("data get 점수") == 10)
    check("function korean name", val("function set 인사 say hi") == 1 and val("function 인사") == 1)

    print("#2 #id targeting + duplicate:")
    val("create chat{name:봇}")
    val("create chat{name:봇}")
    ids = [n for n in plugin.app.nodes]
    second = ids[-1]
    check("#id direct delete", val(f"delete #{second}") == 1)
    check("only first 봇 left", names().count("봇") == 1)
    check("bad #id fails", val("delete #99999") == 0)

    print("#11 unknown param key blocks create:")
    before = len(plugin.app.nodes)
    check("typo key → not created", val("create number{valeu:5}") == 0)
    check("node count unchanged", len(plugin.app.nodes) == before)

    print("#4 type-aware connect (button signal → chat execute):")
    val("create button{name:버튼}")
    val("create chat{name:챗}")
    r = ctrl.execute("connect 버튼 챗")
    check("connect ok", r.value == 1)
    check("picked signal input (⚡)", any("⚡" in m and "실행" in m for m in r.messages))

    print("#10 #12 errors:")
    check("function set edit blocked", val("function set edit foo") == 0)
    check("store without run errors", val("execute store result data x") == 0)

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


def _raises_tag(fn, *a):
    from v.boards.whiteboard.cmd_params import TagSyntaxError
    try:
        fn(*a); return False
    except TagSyntaxError:
        return True


if __name__ == "__main__":
    main()
