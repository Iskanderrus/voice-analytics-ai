#!/usr/bin/env python3
"""End-to-end demo client, standard library only.

Does exactly what the mobile app does: request an upload slot, upload the file
directly to object storage (presigned POST), confirm the upload, start an
analysis, poll its status and print the result.

    VA_TOKEN=... python3 scripts/demo.py [--file scripts/fixtures/sales_call.m4a]
"""

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_FILE = Path(__file__).parent / "fixtures" / "sales_call.m4a"
CONTENT_TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}


def call(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Token {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = response.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        if exc.code in (202, 409) and "result" in url:
            return json.loads(detail)
        sys.exit(f"{method} {url} -> HTTP {exc.code}: {detail}")


def presigned_post(url: str, fields: dict[str, str], path: Path, content_type: str) -> None:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    # The file must be the last form field for S3 presigned POST.
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(url, data=b"".join(parts), method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            assert response.status in (200, 201, 204), response.status
    except urllib.error.HTTPError as exc:
        sys.exit(f"storage upload failed: HTTP {exc.code}: {exc.read().decode()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=os.environ.get("VA_API", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("VA_TOKEN"))
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--profile", default="default", choices=["default", "standard_only"])
    parser.add_argument("--timeout", type=int, default=900, help="seconds to wait for the result")
    args = parser.parse_args()
    if not args.token:
        sys.exit("missing API token: pass --token or set VA_TOKEN (make demo-user prints one)")

    api = args.api.rstrip("/") + "/api/v1"
    path: Path = args.file
    content_type = CONTENT_TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or ""
    size = path.stat().st_size

    print(f"1. requesting upload slot for {path.name} ({size} bytes, {content_type})")
    slot = call(
        "POST",
        f"{api}/uploads",
        args.token,
        {"filename": path.name, "content_type": content_type, "file_size": size},
    )
    print(f"   upload_id={slot['upload_id']} key={slot['object_key']}")

    print(f"2. uploading directly to object storage: {slot['upload_url']}")
    presigned_post(slot["upload_url"], slot["upload_fields"], path, content_type)

    upload = call("POST", f"{api}/uploads/{slot['upload_id']}/complete", args.token)
    print(f"3. upload confirmed by backend: status={upload['status']} etag={upload['checksum']}")

    job = call(
        "POST",
        f"{api}/analyses",
        args.token,
        {"upload_id": slot["upload_id"], "analysis_profile": args.profile},
    )
    print(f"4. analysis {job['id']} created: {job['status']}")

    started, last = time.monotonic(), None
    while True:
        job = call("GET", f"{api}/analyses/{job['id']}", args.token)
        if job["status"] != last:
            print(f"   [{time.monotonic() - started:6.1f}s] {job['status']:<20} stage={job['current_stage']}")
            last = job["status"]
        if job["status"] in ("COMPLETED", "FAILED"):
            break
        if time.monotonic() - started > args.timeout:
            sys.exit("timed out waiting for the analysis")
        time.sleep(2)

    result = call("GET", f"{api}/analyses/{job['id']}/result", args.token)
    if job["status"] == "FAILED":
        print(json.dumps(result, indent=2))
        sys.exit(1)

    print("\n5. result")
    print(f"   transcript ({result['transcript']['provider']}/{result['transcript']['model']}):")
    print(f"     {result['transcript']['text'][:400]}")
    standard = result["standard_analysis"]
    print(f"   standard analysis ({standard['provenance']['provider']}/{standard['provenance']['model']}):")
    print(json.dumps(standard["structured_output"], indent=2))
    custom = result["custom_analysis"]
    if custom:
        template = custom["template"]
        print(f"   template analysis: {template['name']} v{template['version']}")
        print(json.dumps(custom["structured_output"], indent=2))
    else:
        print("   template analysis: none matched")
    print(f"   cost: {json.dumps(result['cost'])}")


if __name__ == "__main__":
    main()
