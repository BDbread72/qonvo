"""
동적 모델 플러그인 시스템
- ModelPlugin: 플러그인 기반 클래스 (ABC)
- PluginRegistry: 플러그인 발견/로드/조회 싱글턴
- ProviderRouter: model_id 기반 provider 라우팅
- get_all_models/get_all_model_ids/get_all_model_options: 통합 접근자
"""
import importlib.util
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Any


class ModelPlugin(ABC):
    """
    모델 플러그인 기반 클래스.
    사용자 플러그인은 이 클래스를 상속하고 PLUGIN_CLASS로 export.

    Example:
        class MyPlugin(ModelPlugin):
            NAME = "OpenAI"
            MODELS = {"gpt-4o": "GPT-4o"}
            def chat(self, model, messages, stream=True, **options): ...

        PLUGIN_CLASS = MyPlugin
    """
    NAME: str = "Unnamed Plugin"
    DESCRIPTION: str = ""
    VERSION: str = "1.0"
    MODELS: Dict[str, str] = {}           # model_id -> display name
    MODEL_OPTIONS: Dict[str, Dict] = {}   # model_id -> option schema

    def configure(self, api_keys: list[str] = None, **kwargs):
        """플러그인 초기화. PluginRegistry가 로드 후 호출."""
        self._api_keys = api_keys or []

    @abstractmethod
    def chat(self, model: str, messages, stream: bool = True, **options):
        """
        채팅 요청.
        - stream=True: Generator[str] 반환 (텍스트 청크)
        - stream=False: str 반환
        - messages: List[ChatMessage] (from v.provider)
        """
        ...

    def chat_candidates(self, model: str, messages, n: int, **options) -> list:
        """Preferred Options용 다중 결과. 기본: chat() n회 호출."""
        return [self.chat(model, messages, stream=False, **options) for _ in range(n)]

    def cancel(self):
        """진행 중 요청 취소 (선택사항)"""
        pass


class ProviderRouter:
    """model_id 기반 provider 라우팅. StreamWorker/FunctionEngine에 투명하게 전달."""

    def __init__(self, gemini_provider=None):
        self._gemini = gemini_provider
        self._registry = PluginRegistry.instance()

    def chat(self, model, messages, stream=True, **options):
        plugin = self._registry.get_plugin_for_model(model)
        if plugin:
            return plugin.chat(model, messages, stream=stream, **options)
        if not self._gemini:
            raise ValueError("Gemini API key not configured")
        return self._gemini.chat(model, messages, stream=stream, **options)

    def chat_candidates(self, model, messages, count, on_job_created=None, **options):
        plugin = self._registry.get_plugin_for_model(model)
        if plugin:
            return plugin.chat_candidates(model, messages, count, **options)
        if not self._gemini:
            raise ValueError("Gemini API key not configured")
        return self._gemini.chat_candidates(
            model, messages, count, on_job_created=on_job_created, **options
        )

    def cancel(self):
        if self._gemini:
            self._gemini.cancel()
        for plugin in self._registry._plugins.values():
            plugin.cancel()

    @property
    def gemini(self):
        """Gemini 전용 기능 접근 (batch resume 등)"""
        return self._gemini


class PluginRegistry:
    """플러그인 발견/로드/조회 싱글턴"""
    _instance = None

    def __init__(self):
        self._plugins: Dict[str, ModelPlugin] = {}     # plugin_id -> instance
        self._model_to_plugin: Dict[str, str] = {}     # model_id -> plugin_id
        self._discovered: Dict[str, dict] = {}         # plugin_id -> metadata

    @classmethod
    def instance(cls) -> "PluginRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load_all(self):
        """plugins 디렉토리 스캔 -> 활성화된 플러그인만 로드"""
        from v.settings import get_enabled_plugins, get_plugin_api_keys

        enabled = set(get_enabled_plugins())

        self._plugins.clear()
        self._model_to_plugin.clear()
        self._discovered.clear()

        # 사용자 plugins 디렉토리 + 번들 plugins 디렉토리
        dirs = []
        user_dir = get_plugins_dir()
        if not user_dir.exists():
            user_dir.mkdir(parents=True, exist_ok=True)
        dirs.append(user_dir)

        bundled = _get_bundled_plugins_dir()
        if bundled:
            dirs.append(bundled)

        for plugins_dir in dirs:
            for py_file in plugins_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                plugin_id = py_file.stem
                if plugin_id in self._discovered:
                    continue  # 사용자 플러그인이 번들보다 우선
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"qonvo_plugin_{plugin_id}", py_file
                    )
                    if not spec or not spec.loader:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    if not hasattr(module, "PLUGIN_CLASS"):
                        continue

                    plugin_cls = module.PLUGIN_CLASS
                    if not (isinstance(plugin_cls, type) and issubclass(plugin_cls, ModelPlugin)):
                        continue

                    # 메타데이터 기록 (활성화 여부 무관)
                    self._discovered[plugin_id] = {
                        "name": getattr(plugin_cls, "NAME", plugin_id),
                        "version": getattr(plugin_cls, "VERSION", "1.0"),
                        "description": getattr(plugin_cls, "DESCRIPTION", ""),
                        "models": dict(getattr(plugin_cls, "MODELS", {})),
                    }

                    # 활성화된 플러그인만 인스턴스 생성
                    if plugin_id in enabled:
                        inst = plugin_cls()
                        plugin_keys = get_plugin_api_keys(plugin_id)
                        try:
                            inst.configure(api_keys=plugin_keys)
                        except Exception as cfg_err:
                            from v.logger import get_logger
                            get_logger("qonvo.plugin").warning(
                                f"Plugin configure failed [{plugin_id}]: {cfg_err}"
                            )
                            # P4: configure 실패 시 모델 등록하지 않음
                            continue
                        self._plugins[plugin_id] = inst
                        for mid in inst.MODELS:
                            self._model_to_plugin[mid] = plugin_id

                except Exception as e:
                    from v.logger import get_logger
                    get_logger("qonvo.plugin").warning(
                        f"Plugin load failed [{plugin_id}]: {e}"
                    )
                    self._discovered[plugin_id] = {
                        "name": plugin_id,
                        "version": "?",
                        "description": f"Load error: {e}",
                        "models": {},
                    }

    def get_plugin_for_model(self, model_id: str) -> ModelPlugin | None:
        """모델 ID에 해당하는 플러그인 인스턴스 반환"""
        pid = self._model_to_plugin.get(model_id)
        return self._plugins.get(pid) if pid else None

    def is_plugin_model(self, model_id: str) -> bool:
        return model_id in self._model_to_plugin

    def get_all_plugin_models(self) -> Dict[str, str]:
        """활성화된 플러그인의 모든 모델 {model_id: display_name}"""
        result = {}
        for p in self._plugins.values():
            result.update(p.MODELS)
        return result

    def get_all_plugin_model_options(self) -> Dict[str, Dict]:
        """활성화된 플러그인의 모든 모델 옵션 스키마"""
        result = {}
        for p in self._plugins.values():
            result.update(p.MODEL_OPTIONS)
        return result

    def get_discovered_plugins(self) -> List[dict]:
        """발견된 모든 플러그인 정보 (UI용)"""
        result = []
        for pid, meta in self._discovered.items():
            result.append({
                "id": pid,
                "name": meta["name"],
                "version": meta["version"],
                "description": meta["description"],
                "models": meta["models"],
                "enabled": pid in self._plugins,
            })
        return result

    def get_used_plugin_ids(self, model_ids: set) -> list:
        """주어진 model_id 집합에서 사용된 플러그인 ID 목록"""
        return list({
            self._model_to_plugin[mid]
            for mid in model_ids
            if mid in self._model_to_plugin
        })

    def is_available(self, plugin_id: str) -> bool:
        """플러그인이 로드되어 있는지 확인"""
        return plugin_id in self._plugins


def get_plugins_dir() -> Path:
    """플러그인 디렉토리 경로 반환"""
    from v.settings import get_app_data_path
    return get_app_data_path() / "plugins"


def _get_bundled_plugins_dir() -> Path | None:
    """앱과 함께 배포된 플러그인 경로"""
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS) / "plugins"
    else:
        base = Path(__file__).parent.parent.parent / "plugins"
    return base if base.exists() else None


# ── 통합 접근자 (built-in + plugin) ──

def get_all_models() -> Dict[str, str]:
    """내장 모델 + 플러그인 모델 통합 딕셔너리"""
    from v.provider import MODELS
    result = dict(MODELS)
    result.update(PluginRegistry.instance().get_all_plugin_models())
    return result


def get_all_model_ids() -> list:
    """내장 + 플러그인 모델 ID 목록"""
    return list(get_all_models().keys())


def get_all_model_options() -> Dict[str, Dict]:
    """내장 + 플러그인 모델 옵션 스키마"""
    from v.provider import MODEL_OPTIONS
    result = dict(MODEL_OPTIONS)
    result.update(PluginRegistry.instance().get_all_plugin_model_options())
    return result
