#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from urllib.parse import quote
from typing import Any, Dict, List, Optional

import requests

API_BASE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/crawl"

RESOURCE_TYPES = [
    "document",
    "stylesheet",
    "image",
    "media",
    "font",
    "script",
    "texttrack",
    "xhr",
    "fetch",
    "prefetch",
    "eventsource",
    "websocket",
    "manifest",
    "signedexchange",
    "ping",
    "cspviolationreport",
    "preflight",
    "other",
]

CRAWL_PURPOSES = ["search", "ai-input", "ai-train"]

CONTENT_USE_LEVELS = ["reference", "full"]

GOTO_WAIT_UNTIL = ["load", "domcontentloaded", "networkidle0", "networkidle2"]

RESULT_STATUSES = ["queued", "completed", "disallowed", "skipped", "errored", "cancelled"]

DEFAULT_MAX_PAGES = 1000

FILE_ROOT_ENV = "CLOUDCRAWL_FILE_ROOT"

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_ORANGE = "\x1b[38;5;208m"
_WHITE = "\x1b[97m"

_BANNER_CLOUD = [
    " ██████╗██╗      ██████╗ ██╗   ██╗██████╗ ",
    "██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗",
    "██║     ██║     ██║   ██║██║   ██║██║  ██║",
    "██║     ██║     ██║   ██║██║   ██║██║  ██║",
    "╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝",
    " ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝ ",
]

_BANNER_CRAWL = [
    " ██████╗██████╗  █████╗ ██╗    ██╗██╗     ",
    "██╔════╝██╔══██╗██╔══██╗██║    ██║██║     ",
    "██║     ██████╔╝███████║██║ █╗ ██║██║     ",
    "██║     ██╔══██╗██╔══██║██║███╗██║██║     ",
    "╚██████╗██║  ██║██║  ██║╚███╔███╔╝███████╗",
    " ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚══════╝",
]


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _ascii_name() -> str:
    if _use_color():
        rows = [
            f"{_ORANGE}{cloud}{_WHITE}{crawl}{_RESET}"
            for cloud, crawl in zip(_BANNER_CLOUD, _BANNER_CRAWL)
        ]
    else:
        rows = [cloud + crawl for cloud, crawl in zip(_BANNER_CLOUD, _BANNER_CRAWL)]
    return "\n".join(rows)


def _looks_like_invocation(candidate: str) -> bool:
    if candidate.startswith("-"):
        return True
    if candidate.startswith("{") and candidate.endswith("}"):
        return True
    if re.fullmatch(r"[a-z][a-z0-9_]*", candidate):
        return True
    return False


_GAP_RE = re.compile(r"^( {2,})(\S(?:.*\S)?)( {2,})(\S.*)$")
_OWN_RE = re.compile(r"^( {2,})(\S(?:.*\S)?)$")
_HEADING_RE = re.compile(r"^([A-Za-z][^\n]*):$")


def _colorize_help(text: str) -> str:
    if not _use_color():
        return text
    lines = []
    for line in text.split("\n"):
        gap_match = _GAP_RE.match(line)
        if gap_match:
            indent, invocation, gap, remainder = gap_match.groups()
            if _looks_like_invocation(invocation):
                lines.append(f"{indent}{_CYAN}{invocation}{_RESET}{gap}{remainder}")
            else:
                lines.append(line)
            continue
        own_match = _OWN_RE.match(line)
        if own_match and _looks_like_invocation(own_match.group(2)):
            indent, invocation = own_match.groups()
            lines.append(f"{indent}{_CYAN}{invocation}{_RESET}")
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            lines.append(f"{_BOLD}{_YELLOW}{heading_match.group(1)}{_RESET}:")
            continue
        if line.startswith("usage:"):
            lines.append(line.replace("usage:", f"{_BOLD}{_GREEN}usage:{_RESET}", 1))
            continue
        lines.append(line)
    return "\n".join(lines)


class ColorHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog, indent_increment=2, max_help_position=24, width=None, banner=False):
        super().__init__(prog, indent_increment, max_help_position, width)
        self._show_banner = banner
        self._has_body = False

    def add_text(self, text):
        if text is not None:
            self._has_body = True
        super().add_text(text)

    def start_section(self, heading):
        self._has_body = True
        super().start_section(heading)

    def format_help(self) -> str:
        text = _colorize_help(super().format_help())
        if self._show_banner and self._has_body:
            text = "\n" + _ascii_name() + "\n\n" + text
        return text


def _root_formatter(prog):
    return ColorHelpFormatter(prog, banner=True)


class CloudCrawlClient:
    def __init__(self, account_id: str, api_token: str, timeout: int = 30) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.timeout = timeout

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _url(self, job_id: Optional[str] = None) -> str:
        base = API_BASE.format(account_id=quote(self.account_id, safe=""))
        if job_id is None:
            return base
        if not job_id:
            raise SystemExit("A crawl job ID is required but was empty.")
        return f"{base}/{quote(job_id, safe='')}"

    def _request(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise SystemExit(f"Network error while contacting Cloudflare API: {exc}") from None

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: requests.Response) -> Dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            snippet = " ".join(response.text.split())
            if len(snippet) > 300:
                snippet = snippet[:300] + "..."
            raise SystemExit(
                f"Cloudflare API returned a non-JSON response ({response.status_code}): "
                f"{snippet or '<empty body>'}"
            ) from None

        if not isinstance(data, dict):
            snippet = " ".join(json.dumps(data).split())
            if len(snippet) > 300:
                snippet = snippet[:300] + "..."
            raise SystemExit(
                f"Cloudflare API returned an unexpected JSON body "
                f"({response.status_code}), expected an object: {snippet}"
            )

        if not response.ok or not data.get("success", True):
            errors = data.get("errors") or []
            message = "; ".join(
                f"{err.get('code')}: {err.get('message')}" for err in errors
            ) or response.text
            raise SystemExit(f"Cloudflare API error ({response.status_code}): {message}")

        return data

    def start_crawl(self, payload: Dict[str, Any]) -> str:
        data = self._request(
            "POST", self._url(), headers=self._headers, data=json.dumps(payload)
        )
        return data.get("result")

    def get_job(self, job_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = self._request(
            "GET", self._url(job_id), headers=self._headers, params=params or {}
        )
        return data.get("result", {})

    def cancel_job(self, job_id: str) -> None:
        self._request("DELETE", self._url(job_id), headers=self._headers)


def fetch_all_records(
    client: CloudCrawlClient,
    job_id: str,
    params: Dict[str, Any],
    max_pages: int = DEFAULT_MAX_PAGES,
) -> Dict[str, Any]:
    params = dict(params)
    merged: Optional[Dict[str, Any]] = None
    records: List[Any] = []
    seen_cursors = set()
    pages = 0

    while True:
        result = client.get_job(job_id, params=params)
        pages += 1
        records.extend(result.get("records", []))
        if merged is None:
            merged = result

        cursor = result.get("cursor")
        if cursor is None:
            break

        marker = repr(cursor)
        if marker in seen_cursors:
            raise SystemExit(
                f"Pagination stopped making progress: the Cloudflare API returned "
                f"a cursor it had already returned ({cursor!r}) after {pages} page(s), "
                f"{len(records)} record(s). Refusing to loop; re-run with "
                f"`results {job_id} --cursor {cursor!r}` to inspect that page directly."
            )
        seen_cursors.add(marker)

        if pages >= max_pages:
            raise SystemExit(
                f"Stopped after the --max-pages limit of {max_pages} page(s) "
                f"({len(records)} record(s) collected) while more pages remained. "
                f"Re-run with a higher --max-pages, or resume from this page with "
                f"`results {job_id} --cursor {cursor!r}`."
            )

        params["cursor"] = cursor

    merged = dict(merged or {})
    merged["records"] = records
    merged.pop("cursor", None)
    return merged


def _confinement_root() -> Optional[str]:
    root = os.environ.get(FILE_ROOT_ENV)
    if not root or not root.strip():
        return None
    return os.path.realpath(os.path.expanduser(root))


def resolve_path(value: str, label: str) -> str:
    if not value or not value.strip():
        raise SystemExit(f"{label}: file path cannot be empty.")

    expanded = os.path.abspath(os.path.expanduser(value))

    root = _confinement_root()
    if root is None:
        return expanded

    real = os.path.realpath(expanded)
    try:
        inside = real == root or os.path.commonpath([real, root]) == root
    except ValueError:
        inside = False
    if not inside:
        raise SystemExit(
            f"{label}: '{value}' resolves to '{real}', which is outside the "
            f"directory allowed by {FILE_ROOT_ENV} ('{root}')."
        )
    return expanded


def load_json_arg(value: str, label: str) -> Any:
    stripped = value.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid inline JSON for {label}: {exc}") from None

    path = resolve_path(value, label)
    if not os.path.isfile(path):
        raise SystemExit(
            f"{label}: '{value}' is neither inline JSON (starting with {{ or [) "
            f"nor an existing file path."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {label} file '{value}': {exc}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Could not read {label} file '{value}': {exc}") from None


def parse_header_args(pairs: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--header expects KEY=VALUE, got: {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"--header expects KEY=VALUE, got: {pair!r}")
        headers[key] = value
    return headers


def _positive_int(value: str) -> int:
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def _non_negative_int(value: str) -> int:
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {ivalue}")
    return ivalue


def _bounded_int(minimum: int, maximum: int):
    def _validator(value: str) -> int:
        try:
            ivalue = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
        if not (minimum <= ivalue <= maximum):
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}, got {ivalue}"
            )
        return ivalue

    return _validator


def _modified_since_timestamp(value: str) -> int:
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0 (a Unix timestamp), got {ivalue}")
    now = int(time.time())
    one_year_ago = now - 365 * 24 * 60 * 60
    if ivalue > now:
        raise argparse.ArgumentTypeError(
            f"must not be in the future (got {ivalue}, current time is {now})"
        )
    if ivalue < one_year_ago:
        raise argparse.ArgumentTypeError(
            f"must be within the last year (got {ivalue}, one year ago was {one_year_ago})"
        )
    return ivalue


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _job_id(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("job ID cannot be empty")
    if not _JOB_ID_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid job ID {value!r}: expected only letters, digits, '-', and '_'"
        )
    return value


def parse_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--account-id",
        default=os.getenv("CLOUDFLARE_ACCOUNT_ID"),
        help="Cloudflare account ID (or set CLOUDFLARE_ACCOUNT_ID)",
    )
    parser.add_argument(
        "--api-token",
        default=None,
        help="Cloudflare API token with Browser Rendering permissions. "
        "Prefer setting CLOUDFLARE_API_TOKEN instead: passing this flag can "
        "leak the token into shell history and process listings.",
    )
    parser.add_argument(
        "--timeout",
        type=_non_negative_int,
        default=30,
        help="HTTP request timeout in seconds (default: 30)",
    )


def parse_cache_ttl_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache-ttl",
        type=_bounded_int(0, 86400),
        metavar="SECONDS",
        help="Cache TTL for this GET request, in seconds (default: 5, max "
        "86400). Use 0 to always fetch fresh, uncached data.",
    )


def ensure_auth(args: argparse.Namespace) -> None:
    if not args.account_id:
        raise SystemExit(
            "Missing account ID. Provide --account-id or set CLOUDFLARE_ACCOUNT_ID."
        )

    token_from_flag = args.api_token is not None
    if not args.api_token:
        args.api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not args.api_token:
        raise SystemExit(
            "Missing API token. Provide --api-token or set CLOUDFLARE_API_TOKEN."
        )
    if token_from_flag:
        print(
            "Warning: passing --api-token on the command line can leak it into "
            "your shell history and process listings (e.g. `ps`). Prefer "
            "setting the CLOUDFLARE_API_TOKEN environment variable instead.",
            file=sys.stderr,
        )


def build_client(args: argparse.Namespace) -> CloudCrawlClient:
    return CloudCrawlClient(args.account_id, args.api_token, args.timeout)


def cmd_start(args: argparse.Namespace) -> None:
    ensure_auth(args)
    client = build_client(args)

    formats: Optional[List[str]] = args.formats or None

    payload: Dict[str, Any] = {"url": args.url}

    if args.limit is not None:
        payload["limit"] = args.limit
    if args.depth is not None:
        payload["depth"] = args.depth
    if formats is not None:
        payload["formats"] = formats
    if args.render is not None:
        payload["render"] = args.render
    if args.source:
        payload["source"] = args.source
    if args.max_age is not None:
        payload["maxAge"] = args.max_age
    if args.modified_since is not None:
        payload["modifiedSince"] = args.modified_since

    options: Dict[str, Any] = {}
    if args.include_external_links:
        options["includeExternalLinks"] = True
    if args.include_subdomains:
        options["includeSubdomains"] = True
    if args.include_patterns:
        options["includePatterns"] = args.include_patterns
    if args.exclude_patterns:
        options["excludePatterns"] = args.exclude_patterns
    if options:
        payload["options"] = options

    if args.crawl_purposes:
        payload["crawlPurposes"] = args.crawl_purposes
    if args.content_use:
        payload["contentUse"] = args.content_use

    wants_json_format = formats is not None and "json" in formats
    json_options: Dict[str, Any] = {}
    if args.json_prompt:
        json_options["prompt"] = args.json_prompt
    if args.json_response_format:
        json_options["response_format"] = load_json_arg(
            args.json_response_format, "--json-response-format"
        )
    if args.json_custom_ai:
        json_options["custom_ai"] = load_json_arg(args.json_custom_ai, "--json-custom-ai")

    if json_options and not wants_json_format:
        raise SystemExit(
            "--json-prompt / --json-response-format / --json-custom-ai require "
            "'json' to be included in --formats (e.g. --formats json)."
        )
    if wants_json_format and not json_options:
        raise SystemExit(
            "--formats json requires jsonOptions: Cloudflare needs at least "
            "one of --json-prompt or --json-response-format to know what to "
            "extract, or the request will fail (or return null/empty data)."
        )
    if json_options:
        payload["jsonOptions"] = json_options

    advanced_used: List[str] = []

    if args.username or args.password:
        if not (args.username and args.password):
            raise SystemExit(
                "--username and --password must both be provided to set 'authenticate'."
            )
        payload["authenticate"] = {"username": args.username, "password": args.password}
        advanced_used.append("authenticate")

    if args.cookies:
        cookies = load_json_arg(args.cookies, "--cookies")
        if not isinstance(cookies, list):
            raise SystemExit("--cookies must be a JSON array of cookie objects.")
        payload["cookies"] = cookies
        advanced_used.append("cookies")

    if args.header:
        payload["setExtraHTTPHeaders"] = parse_header_args(args.header)
        advanced_used.append("setExtraHTTPHeaders")

    goto_options: Dict[str, Any] = {}
    if args.goto_wait_until:
        goto_options["waitUntil"] = args.goto_wait_until
    if args.goto_timeout is not None:
        goto_options["timeout"] = args.goto_timeout
    if goto_options:
        payload["gotoOptions"] = goto_options
        advanced_used.append("gotoOptions")

    if (args.wait_for_selector_timeout is not None or args.wait_for_selector_visible) and not args.wait_for_selector:
        raise SystemExit(
            "--wait-for-selector-timeout/--wait-for-selector-visible require --wait-for-selector."
        )
    if args.wait_for_selector:
        wait_for_selector: Dict[str, Any] = {"selector": args.wait_for_selector}
        if args.wait_for_selector_timeout is not None:
            wait_for_selector["timeout"] = args.wait_for_selector_timeout
        if args.wait_for_selector_visible:
            wait_for_selector["visible"] = True
        payload["waitForSelector"] = wait_for_selector
        advanced_used.append("waitForSelector")

    if args.reject_resource_types:
        payload["rejectResourceTypes"] = args.reject_resource_types
        advanced_used.append("rejectResourceTypes")

    if args.reject_request_pattern:
        payload["rejectRequestPattern"] = args.reject_request_pattern
        advanced_used.append("rejectRequestPattern")

    if args.allow_resource_types:
        payload["allowResourceTypes"] = args.allow_resource_types
        advanced_used.append("allowResourceTypes")

    if args.allow_request_pattern:
        payload["allowRequestPattern"] = args.allow_request_pattern
        advanced_used.append("allowRequestPattern")

    if args.wait_for_timeout is not None:
        payload["waitForTimeout"] = args.wait_for_timeout
        advanced_used.append("waitForTimeout")

    if args.render is False and advanced_used:
        print(
            "Warning: --no-render runs the crawl on Workers without a headless "
            "browser. Cloudflare only supports crawl-specific parameters in "
            "that mode, so the following may be ignored by the API: "
            + ", ".join(advanced_used),
            file=sys.stderr,
        )

    credential_flags = [
        flag
        for flag, used in (
            ("--username/--password", "authenticate" in advanced_used),
            ("--cookies", "cookies" in advanced_used),
            ("--header", "setExtraHTTPHeaders" in advanced_used),
        )
        if used
    ]
    if credential_flags:
        print(
            "Note: values passed via "
            + ", ".join(credential_flags)
            + " are sent to Cloudflare as plaintext JSON fields in the request "
            "body (over HTTPS) so its browser can authenticate to the target "
            "site on your behalf. They leave your machine to a third party - "
            "prefer scoped or short-lived credentials here.",
            file=sys.stderr,
        )

    job_id = client.start_crawl(payload)
    print(job_id)


def cmd_status(args: argparse.Namespace) -> None:
    ensure_auth(args)
    client = build_client(args)
    params: Dict[str, Any] = {}
    if args.lightweight:
        params["limit"] = 1
    if args.cache_ttl is not None:
        params["cacheTTL"] = args.cache_ttl
    result = client.get_job(args.job_id, params=params)
    print(json.dumps(result, indent=2))


def cmd_results(args: argparse.Namespace) -> None:
    ensure_auth(args)
    client = build_client(args)
    params: Dict[str, Any] = {}
    if args.cursor is not None:
        params["cursor"] = args.cursor
    if args.limit is not None:
        params["limit"] = args.limit
    if args.status is not None:
        params["status"] = args.status
    if args.cache_ttl is not None:
        params["cacheTTL"] = args.cache_ttl

    output_path = resolve_path(args.output, "--output") if args.output else None

    if args.all_pages:
        result = fetch_all_records(client, args.job_id, params, max_pages=args.max_pages)
    else:
        result = client.get_job(args.job_id, params=params)

    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise SystemExit(f"Could not write results to '{output_path}': {exc}") from None
    else:
        print(json.dumps(result, indent=2))


def cmd_wait(args: argparse.Namespace) -> None:
    ensure_auth(args)
    client = build_client(args)

    delay = args.delay
    max_attempts = args.max_attempts
    status = None

    poll_params: Dict[str, Any] = {"limit": 1}
    if args.cache_ttl is not None:
        poll_params["cacheTTL"] = args.cache_ttl

    for attempt in range(1, max_attempts + 1):
        result = client.get_job(args.job_id, params=poll_params)
        status = result.get("status")
        print(f"[attempt {attempt}] status={status}", file=sys.stderr)

        if status != "running":
            if args.fetch_results:
                fetch_params: Dict[str, Any] = {}
                if args.cache_ttl is not None:
                    fetch_params["cacheTTL"] = args.cache_ttl
                if args.all_pages:
                    full_result = fetch_all_records(
                        client, args.job_id, fetch_params, max_pages=args.max_pages
                    )
                else:
                    full_result = client.get_job(args.job_id, params=fetch_params)
                print(json.dumps(full_result, indent=2))
            else:
                print(json.dumps(result, indent=2))

            if status != "completed":
                raise SystemExit(
                    f"Crawl job finished with a non-successful status: {status!r}"
                )
            return

        time.sleep(delay)

    raise SystemExit(
        f"Crawl job did not complete within {max_attempts} attempts "
        f"(delay={delay}s). Last status={status!r}"
    )


def cmd_cancel(args: argparse.Namespace) -> None:
    ensure_auth(args)
    client = build_client(args)
    client.cancel_job(args.job_id)
    print(f"Cancelled job {args.job_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudcrawl.py",
        description=(
            "CloudCrawl: CLI wrapper around Cloudflare's Browser Rendering /crawl endpoint.\n\n"
            "Docs: https://developers.cloudflare.com/browser-run/quick-actions/crawl-endpoint/"
        ),
        formatter_class=_root_formatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=False, prog="cloudcrawl.py")

    start_p = subparsers.add_parser(
        "start",
        help="Start a new crawl job",
        formatter_class=ColorHelpFormatter,
    )
    parse_common_args(start_p)
    start_p.add_argument(
        "url",
        help="Starting URL to crawl",
    )
    start_p.add_argument(
        "--limit",
        type=_bounded_int(1, 100000),
        help="Maximum number of pages to crawl (default 10, max 100000, must be >= 1)",
    )
    start_p.add_argument(
        "--depth",
        type=_bounded_int(1, 100000),
        help="Maximum link depth from starting URL (default 100000, max 100000, must be >= 1)",
    )
    start_p.add_argument(
        "--formats",
        nargs="+",
        choices=["html", "markdown", "json"],
        help="Response formats, e.g. --formats markdown json. "
        "'json' requires --json-prompt and/or --json-response-format.",
    )
    render_group = start_p.add_mutually_exclusive_group()
    render_group.add_argument(
        "--render",
        dest="render",
        action="store_true",
        help="Use headless browser rendering (default behavior)",
    )
    render_group.add_argument(
        "--no-render",
        dest="render",
        action="store_false",
        help="Disable rendering for fast HTML fetch",
    )
    start_p.set_defaults(render=None)

    start_p.add_argument(
        "--source",
        choices=["all", "sitemaps", "links"],
        help="Source for discovering URLs (default: all)",
    )
    start_p.add_argument(
        "--max-age",
        type=_bounded_int(0, 604800),
        help="Max seconds to use cached resources (0-604800, default 86400)",
    )
    start_p.add_argument(
        "--modified-since",
        type=_modified_since_timestamp,
        help="Only crawl pages modified since this Unix timestamp in seconds "
        "(must be > 0, not in the future, and within the last year)",
    )
    start_p.add_argument(
        "--include-external-links",
        action="store_true",
        help="Follow links to external domains",
    )
    start_p.add_argument(
        "--include-subdomains",
        action="store_true",
        help="Follow links to subdomains",
    )
    start_p.add_argument(
        "--include-patterns",
        nargs="+",
        metavar="PATTERN",
        help="Only visit URLs matching these wildcard patterns",
    )
    start_p.add_argument(
        "--exclude-patterns",
        nargs="+",
        metavar="PATTERN",
        help="Skip URLs matching these wildcard patterns",
    )

    content_group = start_p.add_argument_group(
        "Content Signals", "Control crawlPurposes / contentUse enforcement."
    )
    content_group.add_argument(
        "--crawl-purposes",
        nargs="+",
        choices=CRAWL_PURPOSES,
        metavar="PURPOSE",
        help="Declared purposes for Content-Signal enforcement "
        "(default per Cloudflare: search, ai-input, and ai-train). Narrow "
        "this if a target site's robots.txt disallows one of the defaults, "
        "e.g. --crawl-purposes search.",
    )
    content_group.add_argument(
        "--content-use",
        choices=CONTENT_USE_LEVELS,
        help="Declared content-use level for the Content-Signal 'use' "
        "directive (default: full). Use 'reference' for sites that set "
        "use=reference in robots.txt.",
    )

    json_group = start_p.add_argument_group(
        "JSON extraction", "Required whenever --formats includes 'json'."
    )
    json_group.add_argument(
        "--json-prompt",
        help="Prompt describing what to extract (jsonOptions.prompt).",
    )
    json_group.add_argument(
        "--json-response-format",
        metavar="JSON_OR_FILE",
        help="Inline JSON or path to a JSON file for jsonOptions.response_format "
        "(a JSON Schema object, per Cloudflare's /json endpoint docs).",
    )
    json_group.add_argument(
        "--json-custom-ai",
        metavar="JSON_OR_FILE",
        help="Inline JSON or path to a JSON file for jsonOptions.custom_ai "
        "(bring-your-own-model configuration).",
    )

    advanced_group = start_p.add_argument_group(
        "Advanced (browser-only)",
        "Standard Browser Rendering params, supported only when rendering "
        "is enabled (i.e. --no-render is not set).",
    )
    advanced_group.add_argument(
        "--username", help="Username for HTTP authentication (pairs with --password)."
    )
    advanced_group.add_argument(
        "--password", help="Password for HTTP authentication (pairs with --username)."
    )
    advanced_group.add_argument(
        "--cookies",
        metavar="JSON_OR_FILE",
        help="Inline JSON or path to a JSON file with an array of cookie objects.",
    )
    advanced_group.add_argument(
        "--header",
        action="append",
        metavar="KEY=VALUE",
        help="Extra HTTP header to send, e.g. --header 'X-API-Key=abc123'. Repeatable.",
    )
    advanced_group.add_argument(
        "--goto-wait-until",
        choices=GOTO_WAIT_UNTIL,
        help="Navigation wait condition (gotoOptions.waitUntil); use "
        "networkidle0/networkidle2 for SPAs that load content dynamically.",
    )
    advanced_group.add_argument(
        "--goto-timeout",
        type=_non_negative_int,
        metavar="MILLISECONDS",
        help="Navigation timeout in milliseconds (gotoOptions.timeout).",
    )
    advanced_group.add_argument(
        "--wait-for-selector",
        metavar="SELECTOR",
        help="CSS selector to wait for before capturing content (waitForSelector.selector).",
    )
    advanced_group.add_argument(
        "--wait-for-selector-timeout",
        type=_non_negative_int,
        metavar="MILLISECONDS",
        help="Timeout in milliseconds for --wait-for-selector.",
    )
    advanced_group.add_argument(
        "--wait-for-selector-visible",
        action="store_true",
        help="Require the selector to be visible, not just present in the DOM.",
    )
    advanced_group.add_argument(
        "--reject-resource-types",
        nargs="+",
        choices=RESOURCE_TYPES,
        metavar="TYPE",
        help="Block these resource types to speed up crawling, e.g. "
        "--reject-resource-types image media font. Only used when rendering.",
    )
    advanced_group.add_argument(
        "--reject-request-pattern",
        nargs="+",
        metavar="REGEX",
        help="Block requests whose URL matches any of these regex patterns.",
    )
    advanced_group.add_argument(
        "--allow-resource-types",
        nargs="+",
        choices=RESOURCE_TYPES,
        metavar="TYPE",
        help="Only allow these resource types; reject rules are applied "
        "first. Only used when rendering.",
    )
    advanced_group.add_argument(
        "--allow-request-pattern",
        nargs="+",
        metavar="REGEX",
        help="Only allow requests whose URL matches any of these regex "
        "patterns; reject rules are applied first.",
    )
    advanced_group.add_argument(
        "--wait-for-timeout",
        type=_bounded_int(0, 120000),
        metavar="MILLISECONDS",
        help="Wait this many milliseconds before capturing content "
        "(waitForTimeout; independent of --wait-for-selector, max 120000).",
    )
    start_p.set_defaults(func=cmd_start)

    status_p = subparsers.add_parser(
        "status",
        help="Get status for a crawl job",
        formatter_class=ColorHelpFormatter,
    )
    parse_common_args(status_p)
    parse_cache_ttl_arg(status_p)
    status_p.add_argument("job_id", type=_job_id, help="Crawl job ID")
    status_p.add_argument(
        "--lightweight",
        action="store_true",
        help="Use limit=1 to keep response small (recommended for polling)",
    )
    status_p.set_defaults(func=cmd_status)

    results_p = subparsers.add_parser(
        "results",
        help="Fetch results for a crawl job",
        formatter_class=ColorHelpFormatter,
    )
    parse_common_args(results_p)
    parse_cache_ttl_arg(results_p)
    results_p.add_argument("job_id", type=_job_id, help="Crawl job ID")
    results_p.add_argument(
        "--cursor",
        help="Cursor for pagination (starting point; ignored beyond the first "
        "request if --all-pages is set)",
    )
    results_p.add_argument(
        "--limit",
        type=_positive_int,
        help="Maximum number of records to return per page (must be >= 1)",
    )
    results_p.add_argument(
        "--status",
        choices=RESULT_STATUSES,
        help="Filter by URL status within the crawl job",
    )
    results_p.add_argument(
        "--all-pages",
        action="store_true",
        help="Automatically follow the pagination cursor and merge every "
        "page's records into one JSON result, instead of returning only "
        "the first page.",
    )
    results_p.add_argument(
        "--max-pages",
        type=_positive_int,
        default=DEFAULT_MAX_PAGES,
        metavar="PAGES",
        help=f"Safety cap on how many pages --all-pages will follow "
        f"(default: {DEFAULT_MAX_PAGES}). Exceeding it is an error, not a "
        f"silent truncation.",
    )
    results_p.add_argument(
        "-o",
        "--output",
        help="Write JSON result to a file instead of stdout",
    )
    results_p.set_defaults(func=cmd_results)

    wait_p = subparsers.add_parser(
        "wait",
        help="Poll until a crawl job finishes",
        formatter_class=ColorHelpFormatter,
    )
    parse_common_args(wait_p)
    parse_cache_ttl_arg(wait_p)
    wait_p.add_argument("job_id", type=_job_id, help="Crawl job ID")
    wait_p.add_argument(
        "--delay",
        type=_non_negative_int,
        default=5,
        help="Delay between polls in seconds (default: 5)",
    )
    wait_p.add_argument(
        "--max-attempts",
        type=_positive_int,
        default=60,
        help="Maximum number of polling attempts (default: 60, must be >= 1)",
    )
    wait_p.add_argument(
        "--fetch-results",
        action="store_true",
        help="After completion, fetch full results instead of only status",
    )
    wait_p.add_argument(
        "--all-pages",
        action="store_true",
        help="With --fetch-results, follow the pagination cursor and merge "
        "every page's records into one JSON result.",
    )
    wait_p.add_argument(
        "--max-pages",
        type=_positive_int,
        default=DEFAULT_MAX_PAGES,
        metavar="PAGES",
        help=f"Safety cap on how many pages --all-pages will follow "
        f"(default: {DEFAULT_MAX_PAGES}).",
    )
    wait_p.set_defaults(func=cmd_wait)

    cancel_p = subparsers.add_parser(
        "cancel",
        help="Cancel a running crawl job",
        formatter_class=ColorHelpFormatter,
    )
    parse_common_args(cancel_p)
    cancel_p.add_argument("job_id", type=_job_id, help="Crawl job ID")
    cancel_p.set_defaults(func=cmd_cancel)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        raise SystemExit(1)
    args.func(args)


if __name__ == "__main__":
    main()
