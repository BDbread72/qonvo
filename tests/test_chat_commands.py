"""채팅 명령 create/connect/run/grep/delete 통합 테스트 (라이브 플러그인).

라이브 WhiteBoardPlugin 을 offscreen 으로 띄워 명령을 실제로 실행한다.
실행:  QT_QPA_PLATFORM=offscreen python tests/test_chat_commands.py
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
    app = QApplication.instance() or QApplication([])  # ref 유지(GC 시 segfault 방지)

    from v.app import App
    from v.boards.whiteboard.plugin import WhiteBoardPlugin
    from v.boards.whiteboard.chat_commands import ChatCommandController

    plugin = WhiteBoardPlugin(App())
    plugin.create_view()

    class UI:
        current_plugin = plugin
    ctrl = ChatCommandController(UI())

    # ExecutionResult.ok = 파싱 성공 여부. 명령의 논리적 성공/실패는 .value(1/0).
    def good(cmd):
        return ctrl.execute(cmd).value == 1

    def fails(cmd):
        return ctrl.execute(cmd).value == 0

    ok = good

    def names():
        return {plugin.get_node_name(nid) for nid in plugin.app.nodes}

    print("create (데이터 태그):")
    check("prompt text+name", ok('create prompt{text:"You are helpful", name:"Sys"}'))
    check("chat name", ok('create chat{name:"Bot"}'))
    check("number value+name", ok('create number{value:7, name:"N"}'))
    check("3 nodes named", names() == {"Sys", "Bot", "N"})

    print("connect:")
    check("connect ok", ok('connect Sys Bot'))
    check("edge created", len(plugin._edges) == 1)
    check("connect missing node fails", fails('connect Sys Nope'))
    check("connect self fails", fails('connect Sys Sys'))
    # 포트 지정 — Sys(prompt) 출력 'prompt' → N(number) 입력 '설정' 명시 연결
    check("connect named port", ok('connect Sys N prompt 설정'))
    check("connect bad port fails", fails('connect Sys N prompt 없는포트'))
    check("port autocomplete", any("설정" in t for t in
          [c.text for c in ctrl.suggest('connect Sys N prompt ', len('connect Sys N prompt '))]))

    print("grep:")
    r = ctrl.execute('grep helpful')
    check("grep hit", r.ok and any("Sys" in m for m in r.messages))
    check("grep miss", fails('grep zzz없음'))
    check("grep by name", good('grep Bot'))

    print("run:")
    check("run prompt", ok('run Sys'))            # 프롬프트 전파(키 불필요)
    check("run missing fails", fails('run Nope'))
    check("run all ok", ok('run all'))

    print("autocomplete (라이브 노드):")
    sug = lambda s: [c.text for c in ctrl.suggest(s, len(s))]
    check("connect suggests node", "Sys" in sug('connect S'))
    check("delete suggests all", set(sug('delete ')) >= {"Sys", "Bot", "N"})

    print("delete:")
    check("delete leaf", ok('delete N'))
    check("node gone", "N" not in names())
    # run all 이 Bot(챗)을 _running 으로 만듦 → 작업 중 노드 삭제는 거부(가드 검증).
    check("delete running blocked", fails('delete Bot') and "Bot" in names())
    # 가드 해제(헤드리스라 워커가 영영 안 끝남) 후 연결된 노드 삭제.
    for nid in list(plugin.app.nodes):
        n = plugin.app.nodes[nid]
        if hasattr(n, "_running"):
            n._running = False
    check("delete connected", ok('delete Bot'))
    check("edge cleaned with node", len(plugin._edges) == 0)
    check("delete missing fails", fails('delete Ghost'))

    print()
    if _fail:
        print(f"FAILED: {_fail} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
