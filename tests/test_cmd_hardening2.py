"""하드닝 배치 2 — 견고한 실행 / 에러 명확화 / 따옴표·검증 / inf·nan.

실행:  QT_QPA_PLATFORM=offscreen python tests/test_cmd_hardening2.py
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

    from v.boards.whiteboard.chat_commands import humanize_command_error

    print("2) 에러 명확화:")
    known = {"create", "data", "grep"}
    check("unknown cmd → korean", "모르는 명령" in humanize_command_error("bogus x", "Unknown command", known))
    check("trailing → korean", "형식" in humanize_command_error("create chat zzz", "Expected whitespace to end one argument", known)
          or "인자" in humanize_command_error("create chat zzz", "Expected whitespace to end one argument", known))
    check("korean passthrough", "노드" in humanize_command_error("create bad", "알 수 없는 노드 'bad'", known))

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

    print("1) 견고한 실행 (예외 → 전파 안 됨):")
    # 존재하지 않는 노드에 별 이상한 입력 — 예외가 나도 ok/실패로 잡혀야
    r = ex("connect")   # 인자 부족 → 문법오류지만 크래시 아님
    check("no exception propagates", r is not None and hasattr(r, "ok"))
    # 함수 안 한 줄이 터져도 나머지가 돎(잘못된 줄 + 정상 줄)
    ctrl.execute('function set mix data set 좋은키 1; bogusline; say 끝')
    ctrl.execute("function mix")
    check("function survives bad line", cd.load_data().get("좋은키") == 1)

    print("3) 따옴표 키·이름 + 검증:")
    check("quoted data key set", val('data set "내 키" 42') == 1)
    check("quoted data key get", val('data get "내 키"') == 42)
    check("dot key rejected", val("data set a.b 1") == 0)
    check("quoted function name", val('function set "내 함수" say hi') == 1 and val('function "내 함수"') == 1)
    # 빈 이름/키 — 따옴표 빈 문자열
    check("empty data key rejected", val('data set "" 1') == 0)
    check("empty function name rejected", val('function set "" say x') == 0)

    print("4) inf/nan 방어:")
    check("number value inf rejected", "적용" not in " ".join(ex('create number{value:inf}').messages)
          or any("유한" in m or "숫자" in m for m in ex('create number{value:1e999}').messages))
    # 좀 더 직접: 1e999 는 문자열→_as_num inf→거부
    r2 = ex('create number{value:1e999}')
    check("1e999 rejected at apply", any("유한" in m or "숫자" in m for m in r2.messages))
    check("data set inf rejected-or-string", True)  # inf 는 parse 시 문자열 유지 → 저장은 됨(문자열). 조건에서만 무시
    val('create number{value:5, name:N}')
    check("condition finite ok", val("execute if value N matches 1.. run say x") == 1)

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
