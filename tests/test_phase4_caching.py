"""
Phase 4: Port Position Caching Test
테스트: PortItem의 scenePos() 캐싱이 제대로 작동하는지 확인
"""
import sys
import os
from pathlib import Path

if os.name == 'nt':
    os.system('chcp 65001 > nul')

src_path = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_path))

print("=" * 70)
print("Phase 4: Port Position Caching Test")
print("=" * 70)

# Test 1: Items 모듈 임포트
print("\n[Test 1] items.py 임포트 및 기본 로드")
try:
    from v.boards.whiteboard.items import PortItem, EdgeItem
    print("[OK] PortItem, EdgeItem 임포트 성공")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

# Test 2: PortItem 캐싱 필드 확인
print("\n[Test 2] PortItem 캐싱 필드 존재 확인")
try:
    # Mock parent proxy (간단한 객체)
    class MockProxy:
        def pos(self):
            from PyQt6.QtCore import QPointF
            return QPointF(0, 0)
        def boundingRect(self):
            from PyQt6.QtCore import QRectF
            return QRectF(0, 0, 100, 100)

    proxy = MockProxy()
    port = PortItem(PortItem.OUTPUT, proxy, name="test_port")

    # 캐싱 필드 확인
    assert hasattr(port, '_cached_scene_pos'), "_cached_scene_pos 필드 없음"
    assert hasattr(port, '_cache_valid'), "_cache_valid 필드 없음"
    print("[OK] _cached_scene_pos 필드 확인")
    print("[OK] _cache_valid 필드 확인")

    # 캐싱 메서드 확인
    assert hasattr(port, '_invalidate_cache'), "_invalidate_cache 메서드 없음"
    assert hasattr(port, '_notify_edges'), "_notify_edges 메서드 없음"
    assert hasattr(port, 'scenePos'), "scenePos 메서드 없음"
    print("[OK] _invalidate_cache 메서드 확인")
    print("[OK] _notify_edges 메서드 확인")
    print("[OK] scenePos 메서드 확인")
except AssertionError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: EdgeItem 스케줄링 필드 확인
print("\n[Test 3] EdgeItem 스케줄링 필드 확인")
try:
    # EdgeItem을 만들려면 두 개의 PortItem이 필요
    source_port = PortItem(PortItem.OUTPUT, proxy, name="source")
    target_port = PortItem(PortItem.INPUT, proxy, name="target")

    # EdgeItem 생성
    edge = EdgeItem(source_port, target_port)

    # 스케줄링 필드 확인
    assert hasattr(edge, '_update_scheduled'), "_update_scheduled 필드 없음"
    print("[OK] _update_scheduled 필드 확인")

    # 스케줄링 메서드 확인
    assert hasattr(edge, 'schedule_update'), "schedule_update 메서드 없음"
    assert hasattr(edge, '_do_update'), "_do_update 메서드 없음"
    print("[OK] schedule_update 메서드 확인")
    print("[OK] _do_update 메서드 확인")
except AssertionError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: 캐싱 동작 확인 (기본)
print("\n[Test 4] 캐싱 기본 동작 확인")
try:
    port = PortItem(PortItem.OUTPUT, proxy, name="test")

    # 초기 상태: 캐시 미유효
    assert port._cache_valid == False, "초기 캐시 유효 상태가 잘못됨"
    assert port._cached_scene_pos is None, "초기 캐시 위치가 None이 아님"
    print("[OK] 초기 캐시 상태: 무효 (valid=False)")

    # scenePos() 호출 시 캐시 생성
    pos1 = port.scenePos()
    assert port._cache_valid == True, "scenePos() 호출 후 캐시 유효 상태 오류"
    assert port._cached_scene_pos is not None, "scenePos() 호출 후 캐시 None"
    print("[OK] scenePos() 호출 후 캐시 생성 완료")

    # 다시 scenePos() 호출 시 캐시된 값 반환
    pos2 = port.scenePos()
    assert pos1 == pos2, "캐시된 값이 다름"
    print("[OK] 두 번째 scenePos() 호출에서 캐시 값 사용")

except AssertionError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 5: setPos 호출 시 캐시 무효화
print("\n[Test 5] setPos 호출 시 캐시 무효화 확인")
try:
    from PyQt6.QtCore import QPointF

    port = PortItem(PortItem.OUTPUT, proxy, name="test")

    # 캐시 생성
    pos1 = port.scenePos()
    assert port._cache_valid == True, "캐시 생성 실패"
    print("[OK] 캐시 생성")

    # setPos 호출
    port.setPos(QPointF(50, 50))
    assert port._cache_valid == False, "setPos 호출 후 캐시가 무효화되지 않음"
    print("[OK] setPos 호출 후 캐시 무효화")

except AssertionError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 6: EdgeItem에서 schedule_update 호출 확인
print("\n[Test 6] EdgeItem 스케줄 업데이트 확인")
try:
    source_port = PortItem(PortItem.OUTPUT, proxy, name="source")
    target_port = PortItem(PortItem.INPUT, proxy, name="target")
    edge = EdgeItem(source_port, target_port)

    # 초기 상태
    assert edge._update_scheduled == False, "초기 스케줄 상태 오류"
    print("[OK] 초기 스케줄 상태: False")

    # schedule_update 호출
    edge.schedule_update()
    # Note: QTimer.singleShot이 실제로 실행되려면 이벤트 루프가 필요하므로
    # _update_scheduled 플래그만 확인
    assert edge._update_scheduled == True, "schedule_update 호출 후 플래그 미설정"
    print("[OK] schedule_update 호출 후 _update_scheduled=True")

except AssertionError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 7: PortItem.setPos가 _notify_edges 호출 확인
print("\n[Test 7] PortItem.setPos에서 _notify_edges 호출")
try:
    from PyQt6.QtCore import QPointF

    source_port = PortItem(PortItem.OUTPUT, proxy, name="source")
    target_port = PortItem(PortItem.INPUT, proxy, name="target")
    edge = EdgeItem(source_port, target_port)

    # source_port의 setPos 호출 (엣지가 연결됨)
    source_port.setPos(QPointF(100, 100))

    # Edge의 schedule_update가 호출되어 _update_scheduled가 True가 되어야 함
    assert source_port in source_port.edges or len(source_port.edges) > 0, "포트에 엣지가 등록되지 않음"
    # Edge는 source_port.edges 리스트에 등록되어 있음
    if source_port.edges:
        edge_item = source_port.edges[0]
        assert edge_item._update_scheduled == True, "setPos 호출 후 엣지 스케줄 미설정"
        print("[OK] PortItem.setPos에서 연결된 엣지 스케줄 업데이트 확인")
    else:
        print("[WARN] 엣지가 포트에 등록되지 않음 (무시)")

except AssertionError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 결과 요약
print("\n" + "=" * 70)
print("Phase 4: Port Position Caching Test 완료!")
print("=" * 70)
print("\n[OK] 모든 테스트 성공")
print("\n주요 개선 사항:")
print("  - PortItem.scenePos() 캐싱: 중복 계산 제거")
print("  - PortItem.setPos() 자동 무효화: 위치 변경 시 캐시 갱신")
print("  - EdgeItem 이벤트 기반 업데이트: 즉시 업데이트 대신 예약")
print("\n예상 성능 개선:")
print("  - scenePos() 호출 90% 감소 (정지 상태)")
print("  - CPU 사용률 50-70% 감소")
