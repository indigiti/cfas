from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
PUBLIC = RELEASE / "public"
PRIVATE = RELEASE / "private"
CI_RUNTIME = ROOT / ".ci-runtime"


def run(*args: str, env: dict[str, str] | None = None, input_data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=ROOT, env=env, input=input_data, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def copy(src: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / src, dst)


def main() -> None:
    shutil.rmtree(RELEASE, ignore_errors=True)
    shutil.rmtree(CI_RUNTIME, ignore_errors=True)
    for path in (
        PUBLIC / "static",
        PRIVATE / "templates",
        PRIVATE / "static",
        PRIVATE / "scripts",
        PRIVATE / "packages" / "runtime",
        PRIVATE / "bootstrap",
        CI_RUNTIME / "run" / "tmp",
        CI_RUNTIME / ".runtime-home",
    ):
        path.mkdir(parents=True, exist_ok=True)

    for filename in (
        "app.py", "runtime_extensions.py", "wsgi.py", "passenger_wsgi.py",
        "deploy/private/scripts/cgi-gateway.py",
        "deploy/private/scripts/digiops-runtime-activate.py",
        "deploy/private/scripts/install-face-engine.py",
    ):
        py_compile.compile(str(ROOT / filename), doraise=True)

    run("php", "-l", "deploy/public/index.php")
    run("node", "--check", "static/face-setup.js")

    copy("deploy/public/index.php", PUBLIC / "index.php")
    copy("deploy/public/.htaccess", PUBLIC / ".htaccess")
    for name in ("app.js", "styles.css", "admin.css", "face-setup.js"):
        copy(f"static/{name}", PUBLIC / "static" / name)

    for name in ("app.py", "runtime_extensions.py", "wsgi.py", "passenger_wsgi.py", "requirements.txt", "requirements-runtime.txt", "requirements-face.txt"):
        copy(name, PRIVATE / name)
    copy("templates/index.html", PRIVATE / "templates" / "index.html")
    for name in ("app.js", "styles.css", "admin.css"):
        copy(f"static/{name}", PRIVATE / "static" / name)
    for name in ("cgi-gateway.py", "digiops-runtime-activate.py", "install-face-engine.py"):
        copy(f"deploy/private/scripts/{name}", PRIVATE / "scripts" / name)

    run(
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "--no-compile", "--upgrade", "--target", str(PRIVATE / "packages" / "runtime"),
        "-r", "requirements-runtime.txt",
    )
    run(
        sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
        "--only-binary=:all:", "--no-deps", "--dest", str(PRIVATE / "bootstrap"),
        "pip>=25,<26",
    )

    release_meta = {
        "artifact": "digiops-release",
        "repository": os.environ.get("GITHUB_REPOSITORY", "indigiti/cfas"),
        "branch": os.environ.get("GITHUB_REF_NAME", "main"),
        "commit": os.environ.get("GITHUB_SHA", "local"),
        "application_url": "/cfas/",
        "public_path": "public_html/cfas/",
        "private_path": "private_html/cfas/",
        "public_entrypoint": "index.php",
        "runtime_activation": "scripts/digiops-runtime-activate.py",
        "request_gateway": "scripts/cgi-gateway.py",
        "python_runtime": "lightweight-bundled-core",
        "face_engine": "browser-installed-persistent",
        "persistent_runtime_path": "private_html/.cfas-runtime/",
    }
    (PRIVATE / "RELEASE.json").write_text(json.dumps(release_meta, indent=2) + "\n", encoding="utf-8")

    runtime_env = os.environ.copy()
    runtime_env.update({
        "CFAS_RUNTIME_ROOT": str(CI_RUNTIME),
        "HOME": str(CI_RUNTIME / ".runtime-home"),
        "TMPDIR": str(CI_RUNTIME / "run" / "tmp"),
        "PYTHONPATH": os.pathsep.join([str(PRIVATE / "packages" / "runtime"), str(PRIVATE)]),
    })
    run(sys.executable, "-S", "-c", "import flask, PIL, runtime_extensions", env=runtime_env)

    cgi_env = runtime_env.copy()
    cgi_env.update({
        "REQUEST_METHOD": "GET", "QUERY_STRING": "", "CONTENT_TYPE": "", "CONTENT_LENGTH": "0",
        "SCRIPT_NAME": "/cfas", "PATH_INFO": "/api/config", "REQUEST_URI": "/cfas/api/config",
        "SERVER_NAME": "localhost", "SERVER_PORT": "443", "SERVER_PROTOCOL": "HTTP/1.1",
        "SERVER_SOFTWARE": "CI", "GATEWAY_INTERFACE": "CGI/1.1", "REMOTE_ADDR": "127.0.0.1",
        "HTTPS": "on", "HTTP_HOST": "localhost", "HTTP_X_FORWARDED_HOST": "localhost",
        "HTTP_X_FORWARDED_PROTO": "https", "HTTP_X_FORWARDED_PREFIX": "/cfas",
    })
    smoke = run(sys.executable, "-S", str(PRIVATE / "scripts" / "cgi-gateway.py"), env=cgi_env, input_data=b"")
    normalized = smoke.stdout.decode("utf-8", errors="replace").replace(" ", "")
    if "Status:200" not in normalized or '"ok":true' not in normalized or '"face_engine_ready":false' not in normalized:
        raise RuntimeError("CGI smoke test did not return an uninstalled but healthy face-engine state")

    run(
        sys.executable, str(PRIVATE / "scripts" / "digiops-runtime-activate.py"),
        "--expected-commit", release_meta["commit"], env=runtime_env,
    )

    if (PRIVATE / "packages" / "runtime" / "deepface").exists() or (PRIVATE / "packages" / "runtime" / "tensorflow").exists():
        raise RuntimeError("Large face-engine dependencies leaked into the release")
    if not list((PRIVATE / "bootstrap").glob("pip-*.whl")):
        raise RuntimeError("pip bootstrap wheel is missing")
    if not (PUBLIC / "index.php").is_file() or not (PUBLIC / ".htaccess").is_file():
        raise RuntimeError("DigiOps public entrypoint contract is incomplete")

    for file in RELEASE.rglob("*"):
        if file.is_file() and file.stat().st_size > 50 * 1024 * 1024:
            raise RuntimeError(f"Large file unexpectedly included in release: {file}")
    total = sum(file.stat().st_size for file in RELEASE.rglob("*") if file.is_file())
    if total >= 50 * 1024 * 1024:
        raise RuntimeError(f"Release is too large: {total / 1024 / 1024:.1f} MB")

    shutil.rmtree(PRIVATE / "data", ignore_errors=True)
    shutil.rmtree(CI_RUNTIME, ignore_errors=True)
    print(f"DigiOps release ready: {total / 1024 / 1024:.1f} MB before artifact compression")


if __name__ == "__main__":
    main()
