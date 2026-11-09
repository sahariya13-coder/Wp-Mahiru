# YouTube formats API for Mahiru Bot
# Based on the source supplied by the user.
from __future__ import annotations

import html
import ipaddress
import json
import logging
import os
import re
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

AJAX_URL = "https://www.snapyt.app/wp-admin/admin-ajax.php"
SITE_URL = "https://www.snapyt.app/"
USER_AGENT = os.getenv(
    "YTDL_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)
# Render requires the web server to listen on 0.0.0.0.
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
TIMEOUT = float(os.getenv("YTDL_TIMEOUT", "20"))
MAX_HTML_SIZE = int(os.getenv("YTDL_MAX_HTML_SIZE", str(5 * 1024 * 1024)))
MAX_URL_LENGTH = 4096
ALLOWED_PREVIEW_HOSTS = {"snapyt.app", "www.snapyt.app"}
CORS_ORIGIN = os.getenv("CORS_ORIGIN", "*")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ytdl")


class UpstreamError(Exception):
    pass


class FormatParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.formats = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "option":
            return

        attributes = dict(attrs)
        raw_id = attributes.get("value")

        if not raw_id or not raw_id.isdecimal():
            return

        format_id = int(raw_id)
        if format_id in self.seen:
            return

        self.seen.add(format_id)
        label = attributes.get("data-label") or f"Format {format_id}"
        size = attributes.get("data-size") or ""
        kind = attributes.get("data-kind") or "video+audio"
        download = attributes.get("data-force")

        self.formats.append(
            {
                "fmt": format_id,
                "label": re.sub(r"\s+", "", html.unescape(label)),
                "size": html.unescape(size),
                "kind": html.unescape(kind),
                "download": html.unescape(download) if download else None,
            }
        )


def validate_video_url(value):
    value = value.strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("A valid video URL is required.")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("The video URL must use HTTP or HTTPS.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed.")

    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None

    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise ValueError("Private or local network addresses are not allowed.")

    return value


def validate_preview_url(value):
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in ALLOWED_PREVIEW_HOSTS
        or parsed.username
        or parsed.password
    ):
        raise UpstreamError("The upstream service returned an invalid preview URL.")
    return value


def upstream_request(url, data=None, referer=SITE_URL, max_bytes=1024 * 1024):
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Origin": SITE_URL.rstrip("/"),
        "X-Requested-With": "XMLHttpRequest",
    }
    body = None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        body = urlencode(data).encode("utf-8")

    request = Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise UpstreamError("The upstream response exceeded the size limit.")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise UpstreamError("The upstream response exceeded the size limit.")
            return payload, response.headers
    except HTTPError as exc:
        raise UpstreamError(f"Upstream HTTP error: {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpstreamError("The upstream service could not be reached.") from exc


def request_json(data):
    raw, _ = upstream_request(AJAX_URL, data=data)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpstreamError("The upstream service returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise UpstreamError("The upstream service returned an invalid response.")
    return payload


def get_nonce():
    payload = request_json({"action": "vd_fresh_nonce"})
    nonce = payload.get("data", {}).get("nonce") if payload.get("success") else None
    if not isinstance(nonce, str) or not nonce:
        raise UpstreamError("Could not obtain an upstream security token.")
    return nonce


def submit_video(video_url, nonce):
    payload = request_json(
        {
            "action": "process_video_url",
            "video_url": video_url,
            "security": nonce,
        }
    )
    redirect_url = payload.get("data", {}).get("redirect") if payload.get("success") else None
    if not isinstance(redirect_url, str) or not redirect_url:
        raise UpstreamError("The upstream service rejected the video URL.")
    return validate_preview_url(redirect_url)


def get_preview_html(preview_url):
    raw, headers = upstream_request(
        preview_url, referer=SITE_URL, max_bytes=MAX_HTML_SIZE
    )
    content_type = headers.get("Content-Type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise UpstreamError("The upstream preview did not return HTML.")

    charset_match = re.search(r"charset=([\w.-]+)", content_type)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_formats(video_url):
    nonce = get_nonce()
    preview_url = submit_video(video_url, nonce)
    preview_html = get_preview_html(preview_url)
    parser = FormatParser()
    parser.feed(preview_html)
    parser.close()
    return parser.formats


class YTDLHandler(BaseHTTPRequestHandler):
    server_version = "Mahiru-YTDL/1.0"

    def log_message(self, format_string, *args):
        logger.info("%s - %s", self.address_string(), format_string % args)

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.send_json(200, {"success": True, "status": "ok"})
            return

        if parsed.path not in {"/", "/api/formats"}:
            self.send_json(404, {"success": False, "error": "Not Found"})
            return

        values = parse_qs(parsed.query).get("url")
        if not values or not values[0].strip():
            self.send_json(
                400,
                {"success": False, "error": "Missing required query parameter: url"},
            )
            return

        try:
            video_url = validate_video_url(values[0])
            formats = extract_formats(video_url)
            self.send_json(200, {"success": True, "formats": formats})
        except ValueError as exc:
            self.send_json(400, {"success": False, "error": str(exc)})
        except UpstreamError as exc:
            logger.warning("Upstream request failed: %s", exc)
            self.send_json(502, {"success": False, "error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            logger.info("Client disconnected.")
        except Exception:
            logger.exception("Unexpected extraction failure")
            try:
                self.send_json(
                    500,
                    {"success": False, "error": "An unexpected server error occurred."},
                )
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    server = ThreadingHTTPServer((HOST, PORT), YTDLHandler)
    server.daemon_threads = True
    logger.info("Mahiru YouTube API listening on %s:%s", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
