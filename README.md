# Campus Face Attendance — no database

Small Flask + Alpine.js + GSAP attendance prototype for mobile browsers.

## What it does

- One-time person enrollment with User ID, name and camera capture.
- Stores user metadata in `data/users.json` and the enrollment image in `data/faces/`.
- Verifies a fresh camera capture against the registered face using DeepFace.
- Marks one `PRESENT` record per person/day in `data/attendance.csv`.
- Geolocation is **disabled by default for testing** and the record is labelled `BYPASSED_FOR_TESTING` / `TEST`.
- Live verification images are written only to a temporary file and deleted immediately after comparison.
- Optional DeepFace anti-spoofing can be enabled later.

## Run

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` on the same computer.

For a phone camera, use an HTTPS URL in normal deployment. Browsers generally restrict camera/geolocation to secure contexts; `localhost` is treated specially for local development.

## Dummy test flow

1. Open **Register face**.
2. Enter `TEST001` and a name.
3. Start camera, center one face, then **Capture & register**.
4. Go to **Verify & mark**.
5. Enter `TEST001`, start camera, then **Capture, verify & mark present**.
6. Open **History** to see the CSV-backed record.

## Turn geolocation on later

Set environment variables before starting Flask:

```bash
TEST_MODE=false
ENABLE_GEOLOCATION=true
OFFICE_LAT=18.5204
OFFICE_LNG=73.8567
GEOFENCE_RADIUS_M=100
MAX_GPS_ACCURACY_M=80
python app.py
```

The server performs the actual radius and GPS-accuracy check; it does not trust a browser-side “inside campus” flag.

## Optional anti-spoofing

```bash
ENABLE_ANTI_SPOOFING=true
python app.py
```

This uses DeepFace's anti-spoofing path. Test it on your target devices before relying on it for production attendance.

## Storage

```text
data/
├── users.json
├── attendance.csv
└── faces/
    └── TEST001.jpg
```

There is intentionally no SQL/NoSQL database.

## Important production notes

This is a prototype. Before real deployment, add authentication/admin authorization, CSRF protection, rate limiting, HTTPS, retention/deletion rules for biometric data, backups, audit logging, and an explicit consent/privacy notice. File storage is not suitable for multi-server deployments without additional coordination.
