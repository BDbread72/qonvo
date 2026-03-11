import base64
import json
import os
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from v.settings import get_setting, set_setting
from v.crypto_utils import encrypt_api_key, decrypt_api_key

_VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"

FEATURES = {
    "LABEL_DETECTION": "Labels",
    "TEXT_DETECTION": "OCR",
    "FACE_DETECTION": "Faces",
    "OBJECT_LOCALIZATION": "Objects",
    "SAFE_SEARCH_DETECTION": "Safe Search",
    "LANDMARK_DETECTION": "Landmarks",
    "LOGO_DETECTION": "Logos",
}

DEFAULT_FEATURES = ["LABEL_DETECTION", "TEXT_DETECTION"]


def get_vision_api_key() -> str | None:
    env_key = os.environ.get("VISION_API_KEY")
    if env_key:
        return env_key
    encrypted = get_setting("vision_api_key_encrypted")
    if encrypted:
        try:
            return decrypt_api_key(encrypted)
        except Exception:
            return None
    return None


def save_vision_api_key(key: str):
    encrypted = encrypt_api_key(key)
    set_setting("vision_api_key_encrypted", encrypted)


def has_vision_api_key() -> bool:
    return bool(get_vision_api_key())


def get_vision_features() -> list[str]:
    saved = get_setting("vision_features")
    if isinstance(saved, list) and saved:
        return saved
    return list(DEFAULT_FEATURES)


def save_vision_features(features: list[str]):
    set_setting("vision_features", features)


def analyze(image_path: str, features: list[str] | None = None,
            max_results: int = 20) -> dict[str, Any]:
    api_key = get_vision_api_key()
    if not api_key:
        raise RuntimeError("Vision API key not configured")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    if features is None:
        features = list(DEFAULT_FEATURES)

    feature_list = [
        {"type": feat, "maxResults": max_results}
        for feat in features
    ]

    request_body = {
        "requests": [{
            "image": {"content": image_b64},
            "features": feature_list,
        }]
    }

    url = f"{_VISION_API_URL}?key={api_key}"
    req = Request(
        url,
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vision API error ({e.code}): {body}")
    except URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")

    responses = result.get("responses", [])
    if not responses:
        return {}

    return responses[0]


def parse_results(raw: dict[str, Any]) -> dict[str, str]:
    sections = {}

    if raw.get("error"):
        err = raw["error"]
        sections["Error"] = f"{err.get('code', '')}: {err.get('message', '')}"
        return sections

    labels = raw.get("labelAnnotations", [])
    if labels:
        lines = [f"{a['description']}  ({a.get('score', 0):.0%})" for a in labels]
        sections["Labels"] = "\n".join(lines)

    text_annot = raw.get("textAnnotations", [])
    if text_annot:
        sections["OCR Text"] = text_annot[0].get("description", "")

    faces = raw.get("faceAnnotations", [])
    if faces:
        lines = []
        for i, f in enumerate(faces, 1):
            joy = f.get("joyLikelihood", "UNKNOWN")
            sorrow = f.get("sorrowLikelihood", "UNKNOWN")
            anger = f.get("angerLikelihood", "UNKNOWN")
            surprise = f.get("surpriseLikelihood", "UNKNOWN")
            lines.append(
                f"Face {i}: joy={joy}, sorrow={sorrow}, "
                f"anger={anger}, surprise={surprise}"
            )
        sections["Faces"] = "\n".join(lines)

    objects = raw.get("localizedObjectAnnotations", [])
    if objects:
        lines = [
            f"{o['name']}  ({o.get('score', 0):.0%})"
            for o in objects
        ]
        sections["Objects"] = "\n".join(lines)

    safe = raw.get("safeSearchAnnotation")
    if safe:
        lines = [f"{k}: {v}" for k, v in safe.items()]
        sections["Safe Search"] = "\n".join(lines)

    landmarks = raw.get("landmarkAnnotations", [])
    if landmarks:
        lines = [
            f"{lm['description']}  ({lm.get('score', 0):.0%})"
            for lm in landmarks
        ]
        sections["Landmarks"] = "\n".join(lines)

    logos = raw.get("logoAnnotations", [])
    if logos:
        lines = [
            f"{lg['description']}  ({lg.get('score', 0):.0%})"
            for lg in logos
        ]
        sections["Logos"] = "\n".join(lines)

    return sections
