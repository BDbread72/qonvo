"""두 클라이언트 사이 신호/로직 전파 검증 (레벨 기반, 멱등·수렴).

서버를 안 띄우고, A 의 _send_op 를 B 의 _on_remote_ops 로 직결(서버 브로드캐스트
흉내)해 실제 위젯/포트/엣지로 버튼→전구, 래치 상태 전파/수렴/멱등을 확인한다.

실행: python tests/test_signal_sync.py   (PyQt6 필요, offscreen)
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QPointF          # noqa: E402

_app = QApplication([])

from v.app import App                                        # noqa: E402
from v.boards.whiteboard.plugin import WhiteBoardPlugin      # noqa: E402

results = []


def ok(label, cond):
    results.append((label, bool(cond)))
    print(("  [OK] " if cond else "  [FAIL] ") + label)


class FakeClient:
    """서버 브로드캐스트 흉내 — send_op 를 상대 플러그인의 원격 적용으로 전달."""

    def __init__(self, name):
        self.is_connected = True
        self.username = name
        self.peer = None

    def send_op(self, op_type, target, data=None):
        op = {"op_id": "x", "op_type": op_type, "target": str(target),
              "data": data or {}, "timestamp": 0}
        if self.peer is not None:
            self.peer._on_remote_ops([op], self.username)


def make_client(name):
    p = WhiteBoardPlugin(App())
    p.create_view()
    p._applying_remote_op = False
    p._server_client = FakeClient(name)
    return p


def main():
    a = make_client("A")
    b = make_client("B")
    a._server_client.peer = b
    b._server_client.peer = a

    # 동일 그래프를 양쪽에: button(1)->bulb(2), latch(3)->bulb(4)
    for p in (a, b):
        p.add_button(QPointF(0, 0), node_id=1)
        p.add_bulb(QPointF(200, 0), node_id=2)
        p.add_latch(QPointF(0, 200), node_id=3)
        p.add_bulb(QPointF(200, 200), node_id=4)
        p.create_edge(p.app.nodes[1].signal_output_port, p.app.nodes[2].signal_input_port)
        p.create_edge(p.app.nodes[3].signal_output_port, p.app.nodes[4].signal_input_port)

    assert a.server_mode and b.server_mode, "server_mode not active"

    # 1) 버튼 누름(True 레벨)이 상대 클라 전구를 켠다
    a._on_button_signal(1)
    ok("button press -> local bulb ON", a.app.nodes[2]._lit is True)
    ok("button press -> remote bulb ON (level propagated)", b.app.nodes[2]._lit is True)

    # 펄스 off (타이머 대신 즉시 False 레벨)
    a.set_port_state(a.app.nodes[1].signal_output_port, False)
    ok("button release -> local bulb OFF", a.app.nodes[2]._lit is False)
    ok("button release -> remote bulb OFF", b.app.nodes[2]._lit is False)

    # 2) 래치(상태형) Set on B -> A 의 다운스트림 전구 ON
    b.app.nodes[3].on_signal_a(powered=True)
    b.app.nodes[3]._evaluate()
    ok("latch Set -> B latch ON", b.app.nodes[3]._latched is True)
    ok("latch Set -> B bulb ON", b.app.nodes[4]._lit is True)
    ok("latch Set -> A bulb ON (level propagated)", a.app.nodes[4]._lit is True)

    # 3) apply_sync_data 수렴 백스톱 — drift 한 포트도 doc 상태로 강제 수렴
    a.app.nodes[3].apply_sync_data({"latched": False, "data": None})
    ok("force latch OFF (loss simulation)", a.app.nodes[4]._lit is False)
    a.app.nodes[3].apply_sync_data(b.app.nodes[3].to_dict())
    ok("apply_sync_data restores latch ON", a.app.nodes[3]._latched is True)
    ok("apply_sync_data re-drives output -> A bulb ON (converged)", a.app.nodes[4]._lit is True)

    # 4) 멱등성 — 같은 레벨 op 중복 적용해도 상태 불변
    before = a.app.nodes[2]._lit
    a._remote_node_signal("1", {"powered": False})
    a._remote_node_signal("1", {"powered": False})
    ok("duplicate level op is idempotent", a.app.nodes[2]._lit == before)

    passed = sum(1 for _, c in results if c)
    print(f"\n{passed}/{len(results)} checks passed")
    if passed != len(results):
        print("[FAIL] signal sync checks")
        return 1
    print("[PASS] all signal sync checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
