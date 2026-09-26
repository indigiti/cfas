from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from PIL import Image

try:
    from deepface import DeepFace
except Exception:  # lets the app boot and show a useful error before DeepFace is installed
    DeepFace = None


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FACES_DIR = DATA_DIR / "faces"
USERS_FILE = DATA_DIR / "users.json"
ATTENDANCE_FILE = DATA_DIR / "attendance.csv"

for path in (DATA_DIR, FACES_DIR):
    path.mkdir(parents=True, exist_ok=True)

if not USERS_FILE.exists():
    USERS_FILE.write_text("{}", encoding="utf-8")

if not ATTENDANCE_FILE.exists():
    with ATTENDANCE_FILE.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(
            [
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
        )


app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    TEST_MODE=os.getenv("TEST_MODE", "true").lower() == "true",
    ENABLE_GEOLOCATION=os.getenv("ENABLE_GEOLOCATION", "false").lower() == "true",
    ENABLE_ANTI_SPOOFING=os.getenv("ENABLE_ANTI_SPOOFING", "false").lower() == "true",
    OFFICE_LAT=float(os.getenv("OFFICE_LAT", "18.5204")),
    OFFICE_LNG=float(os.getenv("OFFICE_LNG", "73.8567")),
    GEOFENCE_RADIUS_M=float(os.getenv("GEOFENCE_RADIUS_M", "100")),
    MAX_GPS_ACCURACY_M=float(os.getenv("MAX_GPS_ACCURACY_M", "80")),
    FACE_MODEL=os.getenv("FACE_MODEL", "ArcFace"),
    DETECTOR_BACKEND=os.getenv("DETECTOR_BACKEND", "opencv"),
)

file_lock = threading.Lock()
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,40}$")


def api_error(message: str, status: int = 400):
    return jsonify({"ok": False, "message": message}), status


def load_users() -> dict[str, Any]:
    with file_lock:
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}


def save_users(users: dict[str, Any]) -> None:
    with file_lock:
        tmp = USERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(USERS_FILE)


def decode_data_url(data_url: str) -> Image.Image:
    if not isinstance(data_url, str) or "," not in data_url:
        raise ValueError("Camera image is missing.")
    header, encoded = data_url.split(",", 1)
    if not header.startswith("data:image/"):
        raise ValueError("Unsupported image format.")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Image is too large.")
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    if image.width < 160 or image.height < 160:
        raise ValueError("Image is too small for face verification.")
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


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import atan2, cos, radians, sin, sqrt

    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def verify_location(payload: dict[str, Any]) -> dict[str, Any]:
    if not app.config["ENABLE_GEOLOCATION"]:
        return {
            "verified": True,
            "status": "BYPASSED_FOR_TESTING",
            "distance_m": None,
            "latitude": None,
            "longitude": None,
            "accuracy_m": None,
        }

    try:
        lat = float(payload["latitude"])
        lng = float(payload["longitude"])
        accuracy = float(payload["accuracy"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Location is required when geolocation is enabled.")

    if accuracy > app.config["MAX_GPS_ACCURACY_M"]:
        return {
            "verified": False,
            "status": "GPS_ACCURACY_TOO_LOW",
            "distance_m": None,
            "latitude": lat,
            "longitude": lng,
            "accuracy_m": accuracy,
        }

    distance = haversine_m(
        lat,
        lng,
        app.config["OFFICE_LAT"],
        app.config["OFFICE_LNG"],
    )
    return {
        "verified": distance <= app.config["GEOFENCE_RADIUS_M"],
        "status": "INSIDE_GEOFENCE" if distance <= app.config["GEOFENCE_RADIUS_M"] else "OUTSIDE_GEOFENCE",
        "distance_m": round(distance, 1),
        "latitude": lat,
        "longitude": lng,
        "accuracy_m": accuracy,
    }


def append_attendance(row: dict[str, Any]) -> None:
    fields = [
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
    with file_lock, ATTENDANCE_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writerow(row)


def already_present_today(user_id: str, today: str) -> bool:
    with file_lock:
        if not ATTENDANCE_FILE.exists():
            return False
        with ATTENDANCE_FILE.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("user_id") == user_id and row.get("date") == today and row.get("status") == "PRESENT":
                    return True
    return False


@app.get("/")
def index():
    return render_template(
        "index.html",
        test_mode=app.config["TEST_MODE"],
        geolocation_enabled=app.config["ENABLE_GEOLOCATION"],
        anti_spoofing_enabled=app.config["ENABLE_ANTI_SPOOFING"],
    )


@app.get("/api/config")
def config():
    return jsonify(
        {
            "ok": True,
            "test_mode": app.config["TEST_MODE"],
            "geolocation_enabled": app.config["ENABLE_GEOLOCATION"],
            "anti_spoofing_enabled": app.config["ENABLE_ANTI_SPOOFING"],
            "radius_m": app.config["GEOFENCE_RADIUS_M"],
            "max_accuracy_m": app.config["MAX_GPS_ACCURACY_M"],
            "face_model": app.config["FACE_MODEL"],
        }
    )


@app.post("/api/register")
def register():
    if DeepFace is None:
        return api_error("DeepFace is not installed. Run: pip install -r requirements.txt", 503)

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

    try:
        image = decode_data_url(payload.get("image", ""))
        faces = DeepFace.extract_faces(
            img_path=image,
            detector_backend=app.config["DETECTOR_BACKEND"],
            enforce_detection=True,
            align=True,
            anti_spoofing=app.config["ENABLE_ANTI_SPOOFING"],
        )
        if len(faces) != 1:
            return api_error("Enrollment requires exactly one clearly visible face.")
        if app.config["ENABLE_ANTI_SPOOFING"] and not bool(faces[0].get("is_real", False)):
            return api_error("Liveness check failed. Please use a live camera image.")
    except Exception as exc:
        return api_error(f"Could not validate the enrollment face: {exc}")

    face_path = FACES_DIR / f"{user_id}.jpg"
    save_image_atomic(image, face_path)

    users[user_id] = {
        "user_id": user_id,
        "name": name,
        "face_file": face_path.name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    save_users(users)

    return jsonify({"ok": True, "message": f"{name} registered successfully.", "user": users[user_id]})


@app.post("/api/verify")
def verify():
    if DeepFace is None:
        return api_error("DeepFace is not installed. Run: pip install -r requirements.txt", 503)

    payload = request.get_json(silent=True) or {}
    user_id = str(payload.get("user_id", "")).strip()
    users = load_users()
    user = users.get(user_id)
    if not user:
        return api_error("User ID not found. Register the person first.", 404)

    try:
        location = verify_location(payload)
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
        enrollment_path = FACES_DIR / user["face_file"]
        if not enrollment_path.exists():
            return api_error("Enrollment image is missing for this user.", 500)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=DATA_DIR) as tmp:
            live_path = Path(tmp.name)
        try:
            live_image.save(live_path, "JPEG", quality=92)
            result = DeepFace.verify(
                img1_path=str(enrollment_path),
                img2_path=str(live_path),
                model_name=app.config["FACE_MODEL"],
                detector_backend=app.config["DETECTOR_BACKEND"],
                distance_metric="cosine",
                enforce_detection=True,
                align=True,
                anti_spoofing=app.config["ENABLE_ANTI_SPOOFING"],
                silent=True,
            )
        finally:
            live_path.unlink(missing_ok=True)
    except Exception as exc:
        return api_error(f"Face verification failed: {exc}")

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
    duplicate = already_present_today(user_id, today)

    if not duplicate:
        append_attendance(
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
                "geolocation_enabled": app.config["ENABLE_GEOLOCATION"],
                "location_verified": location["verified"],
                "location_status": location["status"],
                "latitude": location["latitude"] if location["latitude"] is not None else "",
                "longitude": location["longitude"] if location["longitude"] is not None else "",
                "accuracy_m": location["accuracy_m"] if location["accuracy_m"] is not None else "",
                "mode": "TEST" if app.config["TEST_MODE"] else "PRODUCTION",
            }
        )

    return jsonify(
        {
            "ok": True,
            "verified": True,
            "attendance_marked": not duplicate,
            "duplicate": duplicate,
            "message": (
                "Face verified. Attendance marked PRESENT."
                if not duplicate
                else "Face verified. Attendance was already marked today."
            ),
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
def attendance():
    rows: list[dict[str, str]] = []
    with file_lock:
        if ATTENDANCE_FILE.exists():
            with ATTENDANCE_FILE.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))[-50:]
    rows.reverse()
    return jsonify({"ok": True, "rows": rows})


@app.get("/api/users")
def users():
    data = load_users()
    safe = [{"user_id": item["user_id"], "name": item["name"], "created_at": item["created_at"]} for item in data.values()]
    safe.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"ok": True, "users": safe})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
