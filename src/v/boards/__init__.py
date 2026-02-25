"""
보드 플러그인 로더
boards/ 디렉토리에서 플러그인을 동적으로 로드
"""
import sys
import importlib
import importlib.util
from pathlib import Path
from typing import Dict, Type, List

from v.boards.base import BoardPlugin


# 등록된 플러그인 캐시
_plugins: Dict[str, Type[BoardPlugin]] = {}


def discover_plugins() -> Dict[str, Type[BoardPlugin]]:
    """
    boards 디렉토리에서 플러그인 검색
    Returns: {플러그인_이름: 플러그인_클래스}
    """
    global _plugins

    if _plugins:
        return _plugins

    if getattr(sys, 'frozen', False):
        # PyInstaller exe: 직접 import
        _plugins = _discover_frozen()
    else:
        # 일반 실행: 파일 시스템 탐색
        _plugins = _discover_from_filesystem()

    return _plugins


def _discover_frozen() -> Dict[str, Type[BoardPlugin]]:
    """PyInstaller exe 환경에서 플러그인 직접 import"""
    plugins = {}
    try:
        from v.boards.whiteboard import PLUGIN_CLASS
        if issubclass(PLUGIN_CLASS, BoardPlugin):
            plugins["whiteboard"] = PLUGIN_CLASS
    except Exception as e:
        from q import t
        print(t("error.plugin_load_failed", name="whiteboard", error=str(e)))
    return plugins


def _discover_from_filesystem() -> Dict[str, Type[BoardPlugin]]:
    """파일 시스템에서 플러그인 동적 탐색"""
    plugins = {}
    boards_dir = Path(__file__).parent

    # 단일 파일 플러그인 (.py)
    for py_file in boards_dir.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "base.py":
            continue

        module_name = py_file.stem

        try:
            spec = importlib.util.spec_from_file_location(
                f"v.boards.{module_name}",
                py_file
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "PLUGIN_CLASS"):
                    plugin_class = module.PLUGIN_CLASS
                    if issubclass(plugin_class, BoardPlugin):
                        plugins[module_name] = plugin_class
        except Exception as e:
            from q import t
            print(t("error.plugin_load_failed", name=module_name, error=str(e)))

    # 패키지 플러그인 (하위 디렉토리)
    for entry in boards_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        init_file = entry / "__init__.py"
        if not init_file.exists():
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"v.boards.{entry.name}",
                init_file,
                submodule_search_locations=[str(entry)]
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "PLUGIN_CLASS"):
                    plugin_class = module.PLUGIN_CLASS
                    if issubclass(plugin_class, BoardPlugin):
                        plugins[entry.name] = plugin_class
        except Exception as e:
            from q import t
            print(t("error.plugin_load_failed", name=entry.name, error=str(e)))

    return plugins


def get_plugin(name: str) -> Type[BoardPlugin] | None:
    """이름으로 플러그인 클래스 가져오기"""
    plugins = discover_plugins()
    return plugins.get(name)


def get_plugin_list() -> List[Dict[str, str]]:
    """플러그인 목록 (UI용)"""
    plugins = discover_plugins()
    result = []
    for name, cls in plugins.items():
        info = cls.get_info()
        info["id"] = name
        result.append(info)
    return result


def get_plugin_by_type(board_type: str) -> Type[BoardPlugin] | None:
    """
    보드 타입 이름으로 플러그인 찾기
    board_type: "WhiteBoard" 등 저장된 type 값
    """
    plugins = discover_plugins()

    # 타입명 → 플러그인 매핑
    type_map = {
        "WhiteBoard": "whiteboard",
    }

    plugin_name = type_map.get(board_type)
    if plugin_name:
        return plugins.get(plugin_name)

    return None
