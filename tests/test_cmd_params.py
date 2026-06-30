"""create 데이터 태그(`/create <노드>{key:value}`) 회귀 테스트.

헤드리스: 파서 + 자동완성 + 스키마.  offscreen Qt: 적용기(라이브 위젯).
실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_params.py
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
    mark = "OK " if cond else "XX "
    if not cond:
        _fail += 1
    print(f"  [{mark}] {name}")


def main():
    # Qt 위젯 적용기 테스트를 위해 QApplication 을 먼저 만든다(맨 앞이 안전).
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from v.boards.whiteboard.cmd_params import (
        parse_tag, TagSyntaxError, params_for, value_suggestions,
    )

    print("parse_tag:")
    check("quoted string", parse_tag('{model:"a b"}') == {"model": "a b"})
    check("int coerce", parse_tag('{value:42}') == {"value": 42})
    check("float coerce", parse_tag('{b:4.5}') == {"b": 4.5})
    check("bool coerce", parse_tag('{flag:true}') == {"flag": True})
    check("list", parse_tag('{items:[a,b,c]}') == {"items": ["a", "b", "c"]})
    check("multi + name", parse_tag('{a:3, b:4, name:"x"}') == {"a": 3, "b": 4, "name": "x"})
    check("empty", parse_tag('{}') == {})
    check("trailing comma", parse_tag('{a:1,}') == {"a": 1})

    def raises(s):
        try:
            parse_tag(s); return False
        except TagSyntaxError:
            return True
    check("err: no colon", raises('{model}'))
    check("err: no braces", raises('model:x'))
    check("err: unclosed quote", raises('{a:"x}'))

    print("schema:")
    check("math keys", set(params_for("math")) == {"name", "a", "b", "op"})
    check("universal name", "name" in params_for("chat"))
    check("math ops", value_suggestions("math", "op", None)[:3] == ["add", "sub", "mul"])
    check("roles", value_suggestions("prompt", "role", None) == ["system", "user", "assistant"])
    check("sticky colors", "blue" in value_suggestions("sticky", "color", None))

    print("autocomplete (dispatcher):")
    from v.boards.whiteboard.chat_commands import ChatCommandController

    class UI:
        current_plugin = None
    ctrl = ChatCommandController(UI())

    def sug(s):
        return [c.text for c in ctrl.suggest(s, len(s))]
    check("node keys", "chat" in sug("create cha"))
    check("param keys", sug("create chat{") == ["model:", "name:"])
    check("param prefix", sug("create text{te") == ["text:"])
    check("value enum", sug("create math{op:m") == ["max", "min", "mod", "mul"])
    check("unused only", "a" not in [x.rstrip(":") for x in sug("create math{a:3, ")])
    check("value callable", "blue" in sug("create sticky{color:"))
    comps = ctrl.suggest("create chat{mo", len("create chat{mo"))
    check("apply range", bool(comps) and comps[0].apply() == "create chat{model:")
    # 끝에 떨어진 태그(붙임/공백/위치 뒤) 도 자동완성
    check("trailing tag spaced", sug("create text {te") == ["text:"])
    check("trailing tag after loc", sug("create text 0 0 {te") == ["text:"])

    print("appliers (offscreen widgets):")
    from v.boards.whiteboard import cmd_params as cp
    from v.boards.whiteboard.number_node import NumberNodeWidget
    from v.boards.whiteboard.math_node import MathNodeWidget
    from v.boards.whiteboard.markdown_node import MarkdownNodeWidget
    from v.boards.whiteboard.prompt_node import PromptNodeWidget
    from v.boards.whiteboard.sticky_note import StickyNoteWidget
    from v.boards.whiteboard.button_node import ButtonNodeWidget
    from v.boards.whiteboard._node_factory import TextItem

    noop = lambda *a, **k: None
    n = NumberNodeWidget(1, on_value_changed=noop, on_modified=noop)
    cp._num_value(None, n, 42); cp._num_step(None, n, 5)
    check("number", n._value == 42 and n._step == 5.0)

    m = MathNodeWidget(2, on_value_changed=noop, on_modified=noop)
    cp._math_a(None, m, 3); cp._math_b(None, m, 4); cp._math_op(None, m, "mul")
    check("math evaluate", m._result == 12)

    md = MarkdownNodeWidget(on_modified=noop)
    cp._md_text(None, md, "# H")
    check("markdown", md._raw_md == "# H")

    p = PromptNodeWidget(on_modified=noop)
    cp._prompt_text(None, p, "sys"); cp._prompt_role(None, p, "user")
    check("prompt", p.body_edit.toPlainText() == "sys" and p.role_combo.currentData() == "user")

    s = StickyNoteWidget(on_modified=noop)
    cp._sticky_text(None, s, "memo"); cp._sticky_color(None, s, "blue")
    check("sticky", s.body_edit.toPlainText() == "memo" and s.color == "blue")

    b = ButtonNodeWidget(3, on_signal=noop, on_modified=noop)
    cp._button_label(None, b, "go")
    check("button", b._label == "go" and b.title_label.text() == "go")

    t = TextItem(0, 0)
    cp._text_text(None, t, "hi"); cp._text_size(None, t, 20)
    check("text", t.toPlainText() == "hi" and t._font_size == 20)

    def err_raises(fn, *a):
        try:
            fn(None, *a); return False
        except ValueError:
            return True
    check("err: bad op", err_raises(cp._math_op, m, "bogus"))
    check("err: bad num", err_raises(cp._num_value, n, "abc"))

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
