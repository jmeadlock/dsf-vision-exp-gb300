#!/usr/bin/env python3
"""Regression test for secret-safe authenticated curl in the remote runner."""
from __future__ import annotations

import http.server
import re
import subprocess
import threading
import unittest
from pathlib import Path


RUNNER = Path(__file__).with_name("run_remote_candidate.sh")


class Handler(http.server.BaseHTTPRequestHandler):
    observed = ""

    def do_GET(self) -> None:  # noqa: N802
        type(self).observed = self.headers.get("Authorization", "")
        self.send_response(200 if type(self).observed == "Bearer fixture-secret" else 401)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *_args: object) -> None:
        return


class AuthCurlTests(unittest.TestCase):
    def test_runner_auth_curl_sends_header_without_literal_placeholder(self) -> None:
        source = RUNNER.read_text()
        self.assertNotIn("Bearer " + "*" * 3, source)
        match = re.search(r"(?ms)^auth_curl\(\) \{\n.*?^\}\n", source)
        self.assertIsNotNone(match)

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            command = (
                "set -Eeuo pipefail\n"
                "API_KEY=fixture-secret\n"
                f"{match.group(0)}\n"
                f"auth_curl -fsS http://127.0.0.1:{server.server_port}/probe\n"
            )
            completed = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok")
            self.assertEqual(Handler.observed, "Bearer fixture-secret")
        finally:
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
