# Campus Face Attendance

Browser-operated face attendance application built with Flask, Alpine.js and DeepFace.

## Deployment model

The deployed application is intended to be operated entirely from the web UI. There are no environment-variable or terminal steps required for application configuration after deployment.

For the DigiOps/Cloudways layout used by `indigiti/cfas`:

- Public application: `/cfas/` → `public_html/cfas/`
- Private runtime data: `private_html/cfas/data/`
- First-run setup: open the deployed URL in a browser and create the administrator password.

When the code is not under a `public_html` directory, runtime files fall back to the local `data/` directory for development.

## Automated DigiOps release

Every push to `main` runs `.github/workflows/digiops-release.yml` and publishes a GitHub Actions artifact named exactly `digiops-release`, matching the DigiOps application setting.

The workflow:

- validates Python syntax and boots the Flask application with a test client;
- checks that frontend URLs remain relative so `/cfas/` subdirectory deployment is not broken;
- builds a release-only payload with application source, templates, static assets, requirements and WSGI entry points;
- verifies that runtime data, `.env` files and biometric files are not included;
- uploads the payload as the `digiops-release` artifact.

The artifact is intended to be extracted into `public_html/cfas/`. Runtime data is created automatically under `private_html/cfas/data/` and therefore survives replacement of the public release payload.

## Browser setup

On first visit the application shows a setup form. The administrator can configure:

- administrator password
- test/production record mode
- geolocation requirement
- office latitude and longitude
- geofence radius
- maximum accepted GPS error
- DeepFace anti-spoofing

After setup, the **Admin** tab provides the same settings, password change, face enrollment and attendance history. Normal attendance verification remains available without admin sign-in.

## Security and storage

- Enrollment is administrator-only.
- Attendance history and the user list are administrator-only APIs.
- Admin authentication uses a server-side password hash and an HTTPS-only session cookie.
- Browser write requests are restricted to the same origin.
- Security headers deny framing and restrict camera/geolocation to the application origin.
- Live verification images are temporary and deleted after comparison.
- Under the DigiOps layout, biometric/runtime data is automatically stored outside `public_html`.
- Attendance is de-duplicated to one PRESENT record per user/day within the running application process.

Runtime files include:

```text
private_html/cfas/data/
├── .secret_key
├── settings.json
├── users.json
├── attendance.csv
└── faces/
    └── <user_id>.jpg
```

## Server runtime requirements

The release contains `requirements.txt`; dependency installation belongs to the application runtime/deployment platform, not to the browser operator. No SSH or terminal configuration is required by the CFAS application itself.

DeepFace may download model weights when a face model is first initialized. The runtime therefore needs sufficient memory/disk and outbound access for initial model provisioning. If the face engine is unavailable, the web UI reports that state instead of exposing a shell-install instruction.

WSGI entry points are included as `wsgi.py` and `passenger_wsgi.py`, both exporting `application` for deployment tooling.

## Current architecture limits

This remains a single-server, file-backed application. For high concurrency or multiple application servers, move users/attendance to a transactional database or shared service and move biometric files to controlled shared storage. Production deployments should also define retention/deletion policy, consent/privacy notices, backups and operational monitoring for biometric data.
