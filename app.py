from __future__ import annotations

import base64
import csv
import importlib.util
import io
import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime
from urllib.parse import urlsplit
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from flask import Flask, jsonify, render_template, request, session
from PIL import Image, UnidentifiedImageError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent


def resolve_data_dir() -> Path:
    """Keep runtime/biometric data outside public_html when deployed by DigiOps."""
    if BASE_DIR.parent.name == "public_html":
        app_root = BASE_DIR.parent.parent
        return app_root / "private_html" / BASE_DIR.name / "data"
    return BASE_DIR / "data"


DATA_DIR = resolve_data_dir()
FACES_DIR = DATA_DIR / "faces"
USERS_FILE = DATA_DIR / "users.json"
ATTENDANCE_FILE = DATA_DIR / "attendance.csv"
SETTINGS_FILE = DATA_DIR / "settings.json"
SECRET_FILE = DATA_DIR / ".secret_key"

ATTENDANCE_FIELDS = [
    "timestamp",
    "date",
    "user_id",
    "name",
    "status",
    "face_verified",
    "confidence",
    "distance",
    "threshold",
    "geolocation_enabled",
    "location_verified",
    "location_status",
    "latitude",
    "longitude",
    "accuracy_m",
    "mode",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "configured": False,
    "admin_password_hash": "",
    "test_mode": True,
    "enable_geolocation": False,
    "enable_anti_spoofing": False,
    "office_lat": 18.5204,
    "office_lng": 73.8567,
    "geofence_radius_m": 100.0,
    "max_gps_accuracy_m": 80.0,
    "face_model": "ArcFace",
    "detector_backend": "opencv",
}

file_lock = threading.RLock()
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
_DEEPFACE = None
_DEEPFACE_ERROR: str | None = None


def ensure_runtime_files() -> None:
    for path in (DATA_DIR, FACES_DIR):
        path.mkdir(parents=True, exist_ok=True)

    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")

    if not ATTENDANCE_FILE.exists():
        with ATTENDANCE_FILE.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(ATTENDANCE_FIELDS)

    if not SETTINGS_FILE.exists():
        atomic_json_write(SETTINGS_FILE, DEFAULT_SETTINGS)

    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        try:
            SECRET_FILE.chmod(0o600)
        except OSError:
            pass


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


ensure_runtime_files()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = SECRET_FILE.read_text(encoding="utf-8").strip()
app.config.update(
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
)


@app.before_request
def enforce_same_origin_for_writes():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    origin = request.headers.get("Origin")
    if not origin:
        return None
    origin_parts = urlsplit(origin)
    host_parts = urlsplit(request.host_url)
    if (origin_parts.scheme, origin_parts.netloc) != (host_parts.scheme, host_parts.netloc):
        return api_error("Cross-origin write request rejected.", 403)
    return None


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), geolocation=(self)")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-eval'; "
        "style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    return response


def api_error(message: str, status: int = 400):
    return jsonify({"ok": False, "message": message}), status


@app.errorhandler(413)
def payload_too_large(_error):
    return api_error("Image payload is too large.", 413)


def load_settings() -> dict[str, Any]:
    with file_lock:
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
    merged = DEFAULT_SETTINGS.copy()
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    with file_lock:
        atomic_json_write(SETTINGS_FILE, settings)


def public_config() -> dict[str, Any]:
    settings = load_settings()
    return {
        "ok": True,
        "setup_required": not bool(settings.get("configured")),
        "admin_authenticated": bool(session.get("admin_authenticated")),
        "test_mode": bool(settings["test_mode"]),
        "geolocation_enabled": bool(settings["enable_geolocation"]),
        "anti_spoofing_enabled": bool(settings["enable_anti_spoofing"]),
        "office_lat": settings["office_lat"],
        "office_lng": settings["office_lng"],
        "radius_m": settings["geofence_radius_m"],
        "max_accuracy_m": settings["max_gps_accuracy_m"],
        "face_model": settings["face_model"],
        "detector_backend": settings["detector_backend"],
        "face_engine_ready": importlib.util.find_spec("deepface") is not None,
        "runtime_storage": "private" if BASE_DIR.parent.name == "public_html" else "local",
    }


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def validated_settings(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    updated = current.copy()
    updated["test_mode"] = parse_bool(payload.get("test_mode", current["test_mode"]))
    updated["enable_geolocation"] = parse_bool(payload.get("enable_geolocation", current["enable_geolocation"]))
    updated["enable_anti_spoofing"] = parse_bool(payload.get("enable_anti_spoofing", current["enable_anti_spoofing"]))

    try:
        lat = float(payload.get("office_lat", current["office_lat"]))
        lng = float(payload.get("office_lng", current["office_lng"]))
        radius = float(payload.get("geofence_radius_m", current["geofence_radius_m"]))
        accuracy = float(payload.get("max_gps_accuracy_m", current["max_gps_accuracy_m"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Location settings must contain valid numbers.") from exc

    if not -90 <= lat <= 90:
        raise ValueError("Office latitude must be between -90 and 90.")
    if not -180 <= lng <= 180:
        raise ValueError("Office longitude must be between -180 and 180.")
    if not 10 <= radius <= 10_000:
        raise ValueError("Geofence radius must be between 10 and 10,000 metres.")
    if not 5 <= accuracy <= 1_000:
        raise ValueError("Maximum GPS accuracy must be between 5 and 1,000 metres.")

    updated["office_lat"] = lat
    updated["office_lng"] = lng
    updated["geofence_radius_m"] = radius
    updated["max_gps_accuracy_m"] = accuracy
    return updated


F = TypeVar("F", bound=Callable[..., Any])


def admin_required(func: F) -> F:
    @wraps(func)
    def wrapper(*args, **kwargs):
        settings = load_settings()
        if not settings.get("configured"):
            return api_error("Complete browser setup first.", 428)
        if not session.get("admin_authenticated"):
            return api_error("Administrator sign-in required.", 401)
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def load_users() -> dict[str, Any]:
    with file_lock:
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}


def save_users(users: dict[str, Any]) -> None:
    with file_lock:
        atomic_json_write(USERS_FILE, users)


def decode_data_url(data_url: str) -> Image.Image:
    if not isinstance(data_url, str) or "," not in data_url:
        raise ValueError("Camera image is missing.")
    header, encoded = data_url.split(",", 1)
    if header not in {"data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"}:
        raise ValueError("Unsupported camera image format.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Camera image could not be decoded.") from exc
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Image is too large.")
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Camera image is invalid.") from exc
    if image.width < 160 or image.height < 160:
        raise ValueError("Image is too small for face verification.")
    if image.width * image.height > 12_000_000:
        raise ValueError("Image resolution is too large.")
    return image


def save_image_atomic(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="face_", suffix=".jpg", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        image.save(temp_path, "JPEG", quality=92, optimize=True)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def get_deepface():
    global _DEEPFACE, _DEEPFACE_ERROR
    if _DEEPFACE is not None:
        return _DEEPFACE
    if _DEEPFACE_ERROR:
        raise RuntimeError(_DEEPFACE_ERROR)
    try:
        from deepface import DeepFace

        _DEEPFACE = DeepFace
        return _DEEPFACE
    except Exception as exc:
        _DEEPFACE_ERROR = str(exc)
        raise RuntimeError("Face engine is unavailable in this deployment.") from exc


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import atan2, cos, radians, sin, sqrt

    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def verify_location(payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    if not settings["enable_geolocation"]:
        return {
            "verified": True,
            "status": "BYPASSED_FOR_TESTING" if settings["test_mode"] else "DISABLED",
            "distance_m": None,
            "latitude": None,
            "longitude": None,
            "accuracy_m": None,
        }

    try:
        lat = float(payload["latitude"])
        lng = float(payload["longitude"])
        accuracy = float(payload["accuracy"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Location is required when geolocation is enabled.") from exc

    if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 10_000):
        raise ValueError("The browser returned invalid location data.")

    if accuracy > settings["max_gps_accuracy_m"]:
        return {
            "verified": False,
            "status": "GPS_ACCURACY_TOO_LOW",
            "distance_m": None,
            "latitude": lat,
            "longitude": lng,
            "accuracy_m": accuracy,
        }

    distance = haversine_m(lat, lng, settings["office_lat"], settings["office_lng"])
    verified = distance <= settings["geofence_radius_m"]
    return {
        "verified": verified,
        "status": "INSIDE_GEOFENCE" if verified else "OUTSIDE_GEOFENCE",
        "distance_m": round(distance, 1),
        "latitude": lat,
        "longitude": lng,
        "accuracy_m": accuracy,
    }


def append_attendance_if_new(row: dict[str, Any]) -> bool:
    """Append exactly once per user/day within this process."""
    with file_lock:
        if ATTENDANCE_FILE.exists():
            with ATTENDANCE_FILE.open("r", newline="", encoding="utf-8") as handle:
                duplicate = any(
                    existing.get("user_id") == row["user_id"]
                    and existing.get("date") == row["date"]
                    and existing.get("status") == "PRESENT"
                    for existing in csv.DictReader(handle)
                )
                if duplicate:
                    return False
        with ATTENDANCE_FILE.open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=ATTENDANCE_FIELDS).writerow(row)
        return True


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "cfas"})


@app.get("/api/config")
def config():
    return jsonify(public_config())


@app.post("/api/setup")
def setup():
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("admin_password", ""))
    if len(password) < 10:
        return api_error("Administrator password must be at least 10 characters.")

    with file_lock:
        current = load_settings()
        if current.get("configured"):
            return api_error("Setup is already complete.", 409)
        try:
            updated = validated_settings(payload, current)
        except ValueError as exc:
            return api_error(str(exc))
        updated["configured"] = True
        updated["admin_password_hash"] = generate_password_hash(password)
        save_settings(updated)

    session.clear()
    session["admin_authenticated"] = True
    session.permanent = True
    return jsonify({"ok": True, "message": "Setup complete.", "config": public_config()})


@app.post("/api/admin/login")
def admin_login():
    settings = load_settings()
    if not settings.get("configured"):
        return api_error("Complete browser setup first.", 428)

    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password", ""))
    password_hash = str(settings.get("admin_password_hash", ""))
    if not password_hash or not check_password_hash(password_hash, password):
        return api_error("Invalid administrator password.", 401)

    session.clear()
    session["admin_authenticated"] = True
    session.permanent = True
    return jsonify({"ok": True, "message": "Administrator signed in.", "config": public_config()})


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify({"ok": True, "message": "Signed out."})


@app.post("/api/admin/settings")
@admin_required
def update_settings():
    current = load_settings()
    payload = request.get_json(silent=True) or {}
    try:
        updated = validated_settings(payload, current)
    except ValueError as exc:
        return api_error(str(exc))
    save_settings(updated)
    return jsonify({"ok": True, "message": "Settings saved.", "config": public_config()})


@app.post("/api/admin/password")
@admin_required
def update_admin_password():
    current = load_settings()
    payload = request.get_json(silent=True) or {}
    old_password = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", ""))
    if not check_password_hash(str(current.get("admin_password_hash", "")), old_password):
        return api_error("Current password is incorrect.", 401)
    if len(new_password) < 10:
        return api_error("New password must be at least 10 characters.")
    current["admin_password_hash"] = generate_password_hash(new_password)
    save_settings(current)
    return jsonify({"ok": True, "message": "Administrator password updated."})


@app.post("/api/register")
@admin_required
def register():
    payload = request.get_json(silent=True) or {}
    user_id = str(payload.get("user_id", "")).strip()
    name = str(payload.get("name", "")).strip()

    if not USER_ID_RE.fullmatch(user_id):
        return api_error("User ID must be 2-40 characters: letters, numbers, _ or -.")
    if len(name) < 2 or len(name) > 80:
        return api_error("Enter a valid name (2-80 characters).")

    users = load_users()
    if user_id in users:
        return api_error("That User ID is already registered.", 409)

    settings = load_settings()
    try:
        image = decode_data_url(payload.get("image", ""))
        DeepFace = get_deepface()
        faces = DeepFace.extract_faces(
            img_path=image,
            detector_backend=settings["detector_backend"],
            enforce_detection=True,
            align=True,
            anti_spoofing=settings["enable_anti_spoofing"],
        )
        if len(faces) != 1:
            return api_error("Enrollment requires exactly one clearly visible face.")
        if settings["enable_anti_spoofing"] and not bool(faces[0].get("is_real", False)):
            return api_error("Liveness check failed. Please use a live camera image.")
    except ValueError as exc:
        return api_error(str(exc))
    except Exception:
        app.logger.exception("Enrollment face validation failed")
        return api_error("Could not validate the enrollment face. Check the camera image and face engine.", 503)

    face_path = FACES_DIR / f"{user_id}.jpg"
    with file_lock:
        users = load_users()
        if user_id in users:
            return api_error("That User ID is already registered.", 409)
        save_image_atomic(image, face_path)
        users[user_id] = {
            "user_id": user_id,
            "name": name,
            "face_file": face_path.name,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        save_users(users)
        created_user = users[user_id].copy()
    return jsonify({"ok": True, "message": f"{name} registered successfully.", "user": created_user})


@app.post("/api/verify")
def verify():
    settings = load_settings()
    if not settings.get("configured"):
        return api_error("Application setup has not been completed.", 503)

    payload = request.get_json(silent=True) or {}
    user_id = str(payload.get("user_id", "")).strip()
    if not USER_ID_RE.fullmatch(user_id):
        return api_error("Enter a valid User ID.")

    users = load_users()
    user = users.get(user_id)
    if not user:
        return api_error("User ID not found. Ask an administrator to register you.", 404)

    try:
        location = verify_location(payload, settings)
    except ValueError as exc:
        return api_error(str(exc))

    if not location["verified"]:
        return jsonify(
            {
                "ok": True,
                "verified": False,
                "attendance_marked": False,
                "message": "Location verification failed.",
                "location": location,
            }
        )

    try:
        live_image = decode_data_url(payload.get("image", ""))
        enrollment_path = FACES_DIR / str(user["face_file"])
        if not enrollment_path.is_file() or enrollment_path.parent != FACES_DIR:
            return api_error("Enrollment image is missing for this user.", 500)

        DeepFace = get_deepface()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=DATA_DIR) as tmp:
            live_path = Path(tmp.name)
        try:
            live_image.save(live_path, "JPEG", quality=92)
            result = DeepFace.verify(
                img1_path=str(enrollment_path),
                img2_path=str(live_path),
                model_name=settings["face_model"],
                detector_backend=settings["detector_backend"],
                distance_metric="cosine",
                enforce_detection=True,
                align=True,
                anti_spoofing=settings["enable_anti_spoofing"],
                silent=True,
            )
        finally:
            live_path.unlink(missing_ok=True)
    except ValueError as exc:
        return api_error(str(exc))
    except Exception:
        app.logger.exception("Face verification failed")
        return api_error("Face verification could not be completed. Try again with a clear camera image.", 503)

    face_verified = bool(result.get("verified", False))
    confidence = result.get("confidence")
    distance = result.get("distance")
    threshold = result.get("threshold")

    if not face_verified:
        return jsonify(
            {
                "ok": True,
                "verified": False,
                "attendance_marked": False,
                "message": "Face did not match the registered person.",
                "face": {
                    "verified": False,
                    "confidence": confidence,
                    "distance": distance,
                    "threshold": threshold,
                },
                "location": location,
            }
        )

    now = datetime.now().astimezone()
    today = now.date().isoformat()
    attendance_marked = append_attendance_if_new(
        {
            "timestamp": now.isoformat(timespec="seconds"),
            "date": today,
            "user_id": user_id,
            "name": user["name"],
            "status": "PRESENT",
            "face_verified": True,
            "confidence": confidence if confidence is not None else "",
            "distance": distance if distance is not None else "",
            "threshold": threshold if threshold is not None else "",
            "geolocation_enabled": settings["enable_geolocation"],
            "location_verified": location["verified"],
            "location_status": location["status"],
            "latitude": location["latitude"] if location["latitude"] is not None else "",
            "longitude": location["longitude"] if location["longitude"] is not None else "",
            "accuracy_m": location["accuracy_m"] if location["accuracy_m"] is not None else "",
            "mode": "TEST" if settings["test_mode"] else "PRODUCTION",
        }
    )
    duplicate = not attendance_marked

    return jsonify(
        {
            "ok": True,
            "verified": True,
            "attendance_marked": attendance_marked,
            "duplicate": duplicate,
            "message": "Face verified. Attendance marked PRESENT." if not duplicate else "Face verified. Attendance was already marked today.",
            "user": {"user_id": user_id, "name": user["name"]},
            "face": {
                "verified": True,
                "confidence": confidence,
                "distance": distance,
                "threshold": threshold,
            },
            "location": location,
            "timestamp": now.isoformat(timespec="seconds"),
        }
    )


@app.get("/api/attendance")
@admin_required
def attendance():
    rows: list[dict[str, str]] = []
    with file_lock:
        if ATTENDANCE_FILE.exists():
            with ATTENDANCE_FILE.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))[-100:]
    rows.reverse()
    return jsonify({"ok": True, "rows": rows})


@app.get("/api/users")
@admin_required
def users():
    data = load_users()
    safe = [
        {"user_id": item["user_id"], "name": item["name"], "created_at": item["created_at"]}
        for item in data.values()
    ]
    safe.sort(key=lambda item: item["created_at"], reverse=True)
    return jsonify({"ok": True, "users": safe})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
