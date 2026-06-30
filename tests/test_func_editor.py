"""함수 에디터 — Python↔JS 브리지 계약 + validate 회귀 테스트.

WebEngine UI(CodeMirror) 자체는 디스플레이가 필요해 헤드리스에서 못 띄운다.
여기서는 브리지 메서드(저장/불러오기/자동완성/검증)와 자산 존재만 검증한다.
실행:  QT_QPA_PLATFORM=offscreen python tests/test_func_editor.py
"""
import json
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

    from v.boards.whiteboard.chat_commands import ChatCommandController
    from v.boards.whiteboard.function_editor_web import _build_bridge, assets_dir

    class UI:
        current_plugin = None
    ctrl = ChatCommandController(UI())
    b = _build_bridge(ctrl)

    print("assets:")
    check("editor.html bundled", os.path.exists(os.path.join(assets_dir(), "editor.html")))
    check("editor.js bundled", os.path.exists(os.path.join(assets_dir(), "editor.js")))
    check("codemirror vendored",
          os.path.exists(os.path.join(assets_dir(), "vendor", "codemirror.min.js")))

    print("bridge save/load/list:")
    check("names empty", json.loads(b.functionNames()) == [])
    check("save ok", b.saveFunction("greet", 'say hi $(who)') == "")
    check("save empty name fails", b.saveFunction("", "x") != "")
    check("save reserved fails", b.saveFunction("list", "x") != "")
    check("names has greet", json.loads(b.functionNames()) == ["greet"])
    check("load body", b.loadFunction("greet") == "say hi $(who)")
    check("delete ok", b.deleteFunction("greet") == "")
    check("delete missing fails", b.deleteFunction("nope") != "")

    print("bridge suggest:")
    s = json.loads(b.suggest("create cha", 10))
    check("suggest items", any(it["text"] == "chat" for it in s["items"]))
    check("suggest range", s["from"] == 7 and s["to"] == 10)
    s2 = json.loads(b.suggest("", 0))
    check("suggest empty safe", isinstance(s2["items"], list))

    print("bridge validate:")
    check("valid line", b.validateLine('create chat{name:"x"}') == "")
    check("unknown cmd", b.validateLine("boguscmd foo") != "")
    check("incomplete", b.validateLine("create") != "")
    check("macro line skipped", b.validateLine("$say $(x)") == "")
    check("comment skipped", b.validateLine("# note") == "")
    check("trailing junk", b.validateLine("create chat zzz}") != "")

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
