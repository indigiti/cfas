# Campus Face Attendance

Browser-operated face attendance application built with Flask, Alpine.js and DeepFace.

## Deployment model

The deployed application is intended to be operated entirely from the web UI. There are no environment-variable or terminal steps required for application configuration after deployment.

For the DigiOps/Cloudways layout used by `indigiti/cfas`:

- Public application: `/cfas/` → `public_html/cfas/`
- Private runtime data: `private_html/cfas/data/`
- First-run setup: open the deployed URL in a browser and create the administrator password.

When the code is not under a `public_html` directory, runtime files fall back to the local `data/` directory for development.

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

## Server requirements

The release environment still needs to install the packages in `requirements.txt`; that is a deployment/build responsibility rather than an operator setup step. DeepFace may download model weights the first time a model is used, so the deployment must have sufficient memory, disk space and outbound access for that initialization.

WSGI entry points are included as `wsgi.py` and `passenger_wsgi.py` so deployment tooling can import `application` without changing application code.

## Current architecture limits

This remains a single-server, file-backed application. For high concurrency or multiple application servers, move users/attendance to a transactional database or shared service and move biometric files to controlled shared storage. Production deployments should also define retention/deletion policy, consent/privacy notices, backups and operational monitoring for biometric data.
