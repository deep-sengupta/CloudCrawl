"""Test suite for cloudcrawl.

Uses only the standard library. Run with: python3 test_cloudcrawl.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import cloudcrawl

def make_response(status_code=200, json_data=None, text="", raise_json_error=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.text = text
    if raise_json_error:
        resp.json.side_effect = ValueError("No JSON object could be decoded")
    else:
        resp.json.return_value = json_data
    return resp


class TestUrlArgument(unittest.TestCase):
    def test_url_is_positional_not_a_flag(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["start", "--url", "https://example.com"])
        args = parser.parse_args(["start", "https://example.com"])
        self.assertEqual(args.url, "https://example.com")


class TestWaitMaxAttempts(unittest.TestCase):
    def test_max_attempts_zero_rejected_by_argparse(self):
        parser = cloudcrawl.build_parser()
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    ["wait", "job123", "--max-attempts", "0", "--account-id", "a", "--api-token", "t"]
                )
        self.assertIn("must be >= 1", stderr.getvalue())

    def test_zero_iteration_loop_exits_cleanly(self):
        args = MagicMock()
        args.account_id = "acct"
        args.api_token = "tok"
        args.job_id = "job123"
        args.delay = 0
        args.max_attempts = 0
        args.fetch_results = False
        args.all_pages = False
        args.cache_ttl = None

        with patch.object(cloudcrawl, "build_client") as build_client:
            client = MagicMock()
            build_client.return_value = client
            try:
                cloudcrawl.cmd_wait(args)
            except UnboundLocalError:
                self.fail("cmd_wait raised UnboundLocalError instead of a clean SystemExit")
            except SystemExit as exc:
                self.assertIn("Last status=None", str(exc))


class TestErrorHandling(unittest.TestCase):
    def test_non_json_error_response_is_clean_system_exit(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        resp = make_response(status_code=403, text="<html>Forbidden</html>", raise_json_error=True)
        with patch("requests.request", return_value=resp):
            with self.assertRaises(SystemExit) as ctx:
                client.start_crawl({"url": "https://example.com"})
        self.assertIn("non-JSON response", str(ctx.exception))
        self.assertIn("403", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, ValueError)

    def test_non_json_ok_response_does_not_double_fail_with_traceback(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        resp = make_response(status_code=200, text="not json", raise_json_error=True)
        with patch("requests.request", return_value=resp):
            with self.assertRaises(SystemExit) as ctx:
                client.get_job("job123")
        self.assertIn("non-JSON response", str(ctx.exception))

    def test_connection_error_is_clean_system_exit(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        import requests as real_requests

        with patch("requests.request", side_effect=real_requests.exceptions.ConnectionError("boom")):
            with self.assertRaises(SystemExit) as ctx:
                client.start_crawl({"url": "https://example.com"})
        self.assertIn("Network error", str(ctx.exception))

    def test_timeout_is_clean_system_exit(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        import requests as real_requests

        with patch("requests.request", side_effect=real_requests.exceptions.Timeout("slow")):
            with self.assertRaises(SystemExit):
                client.get_job("job123")

    def test_valid_json_error(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        resp = make_response(
            status_code=400,
            json_data={"success": False, "errors": [{"code": 1003, "message": "bad url"}]},
        )
        with patch("requests.request", return_value=resp):
            with self.assertRaises(SystemExit) as ctx:
                client.start_crawl({"url": "not-a-url"})
        self.assertIn("1003", str(ctx.exception))
        self.assertIn("bad url", str(ctx.exception))

    def test_success_path(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        resp = make_response(status_code=200, json_data={"success": True, "result": "job-abc"})
        with patch("requests.request", return_value=resp):
            job_id = client.start_crawl({"url": "https://example.com"})
        self.assertEqual(job_id, "job-abc")


class TestJsonFormatOptions(unittest.TestCase):
    def run_start(self, argv):
        parser = cloudcrawl.build_parser()
        return parser.parse_args(argv)

    def test_formats_json_without_prompt_or_schema_errors(self):
        args = self.run_start(
            ["start", "https://example.com", "--formats", "json",
             "--account-id", "a", "--api-token", "t"]
        )
        with patch.object(cloudcrawl, "build_client") as build_client:
            build_client.return_value = MagicMock()
            with self.assertRaises(SystemExit) as ctx:
                cloudcrawl.cmd_start(args)
        self.assertIn("requires jsonOptions", str(ctx.exception))

    def test_json_prompt_without_json_format_errors(self):
        args = self.run_start(
            ["start", "https://example.com", "--json-prompt", "extract stuff",
             "--account-id", "a", "--api-token", "t"]
        )
        with patch.object(cloudcrawl, "build_client") as build_client:
            build_client.return_value = MagicMock()
            with self.assertRaises(SystemExit) as ctx:
                cloudcrawl.cmd_start(args)
        self.assertIn("--formats", str(ctx.exception))
        self.assertIn("json", str(ctx.exception))

    def test_formats_json_with_prompt_builds_payload(self):
        args = self.run_start(
            ["start", "https://example.com", "--formats", "json",
             "--json-prompt", "Extract the title",
             "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        with patch.object(cloudcrawl, "build_client", return_value=client):
            cloudcrawl.cmd_start(args)
        payload = client.start_crawl.call_args[0][0]
        self.assertEqual(payload["jsonOptions"], {"prompt": "Extract the title"})
        self.assertEqual(payload["formats"], ["json"])

    def test_json_response_format_inline(self):
        schema = '{"type": "json_schema", "json_schema": {"name": "x", "properties": {}}}'
        args = self.run_start(
            ["start", "https://example.com", "--formats", "json",
             "--json-response-format", schema,
             "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        with patch.object(cloudcrawl, "build_client", return_value=client):
            cloudcrawl.cmd_start(args)
        payload = client.start_crawl.call_args[0][0]
        self.assertEqual(payload["jsonOptions"]["response_format"]["type"], "json_schema")


class TestContentSignals(unittest.TestCase):
    def test_crawl_purposes_and_content_use_in_payload(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["start", "https://example.com", "--crawl-purposes", "search",
             "--content-use", "reference", "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        with patch.object(cloudcrawl, "build_client", return_value=client):
            cloudcrawl.cmd_start(args)
        payload = client.start_crawl.call_args[0][0]
        self.assertEqual(payload["crawlPurposes"], ["search"])
        self.assertEqual(payload["contentUse"], "reference")

    def test_invalid_crawl_purpose_rejected_by_argparse(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--crawl-purposes", "bogus",
                 "--account-id", "a", "--api-token", "t"]
            )


class TestAdvancedParams(unittest.TestCase):
    def run_start(self, argv):
        parser = cloudcrawl.build_parser()
        return parser.parse_args(argv)

    def test_username_without_password_errors(self):
        args = self.run_start(
            ["start", "https://example.com", "--username", "u",
             "--account-id", "a", "--api-token", "t"]
        )
        with patch.object(cloudcrawl, "build_client", return_value=MagicMock()):
            with self.assertRaises(SystemExit):
                cloudcrawl.cmd_start(args)

    def test_authenticate_cookies_headers_goto_wait_for_selector(self):
        cookies_json = '[{"name": "session", "value": "abc", "domain": "example.com"}]'
        args = self.run_start(
            [
                "start", "https://example.com",
                "--username", "u", "--password", "p",
                "--cookies", cookies_json,
                "--header", "X-API-Key=secret", "--header", "X-Other=val",
                "--goto-wait-until", "networkidle0", "--goto-timeout", "60000",
                "--wait-for-selector", "[data-loaded]",
                "--wait-for-selector-timeout", "30000",
                "--wait-for-selector-visible",
                "--reject-resource-types", "image", "font",
                "--reject-request-pattern", "\\.png$",
                "--account-id", "a", "--api-token", "t",
            ]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        with patch.object(cloudcrawl, "build_client", return_value=client):
            cloudcrawl.cmd_start(args)
        payload = client.start_crawl.call_args[0][0]
        self.assertEqual(payload["authenticate"], {"username": "u", "password": "p"})
        self.assertEqual(payload["cookies"][0]["name"], "session")
        self.assertEqual(payload["setExtraHTTPHeaders"], {"X-API-Key": "secret", "X-Other": "val"})
        self.assertEqual(payload["gotoOptions"], {"waitUntil": "networkidle0", "timeout": 60000})
        self.assertEqual(
            payload["waitForSelector"],
            {"selector": "[data-loaded]", "timeout": 30000, "visible": True},
        )
        self.assertEqual(payload["rejectResourceTypes"], ["image", "font"])
        self.assertEqual(payload["rejectRequestPattern"], ["\\.png$"])

    def test_wait_for_selector_timeout_without_selector_errors(self):
        args = self.run_start(
            ["start", "https://example.com", "--wait-for-selector-timeout", "1000",
             "--account-id", "a", "--api-token", "t"]
        )
        with patch.object(cloudcrawl, "build_client", return_value=MagicMock()):
            with self.assertRaises(SystemExit):
                cloudcrawl.cmd_start(args)

    def test_no_render_warns_when_advanced_params_used(self):
        args = self.run_start(
            ["start", "https://example.com", "--no-render",
             "--reject-resource-types", "image",
             "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        stderr = io.StringIO()
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("sys.stderr", stderr):
                cloudcrawl.cmd_start(args)
        self.assertIn("no-render", stderr.getvalue())
        self.assertIn("rejectResourceTypes", stderr.getvalue())


class TestApiTokenWarning(unittest.TestCase):
    def test_warns_when_token_passed_as_flag(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["status", "job1", "--account-id", "a", "--api-token", "t"]
        )
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            cloudcrawl.ensure_auth(args)
        self.assertIn("Warning", stderr.getvalue())
        self.assertIn("shell history", stderr.getvalue())

    def test_no_warning_when_token_from_env(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(["status", "job1", "--account-id", "a"])
        stderr = io.StringIO()
        with patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "env-token"}):
            with patch("sys.stderr", stderr):
                cloudcrawl.ensure_auth(args)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(args.api_token, "env-token")

    def test_missing_token_errors(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(["status", "job1", "--account-id", "a"])
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                cloudcrawl.ensure_auth(args)


class TestPagination(unittest.TestCase):
    def test_fetch_all_records_follows_cursor(self):
        responses = [
            {"id": "j1", "status": "completed", "records": [{"url": "a"}], "cursor": 10},
            {"id": "j1", "status": "completed", "records": [{"url": "b"}], "cursor": 20},
            {"id": "j1", "status": "completed", "records": [{"url": "c"}]},
        ]
        seen_params = []

        def fake_get_job(job_id, params=None):
            seen_params.append(dict(params or {}))
            return responses[len(seen_params) - 1]

        client = MagicMock()
        client.get_job.side_effect = fake_get_job

        result = cloudcrawl.fetch_all_records(client, "j1", {"limit": 1})
        self.assertEqual([r["url"] for r in result["records"]], ["a", "b", "c"])
        self.assertNotIn("cursor", result)
        self.assertEqual(client.get_job.call_count, 3)
        self.assertNotIn("cursor", seen_params[0])
        self.assertEqual(seen_params[1]["cursor"], 10)
        self.assertEqual(seen_params[2]["cursor"], 20)

    def test_results_command_all_pages_flag(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["results", "job1", "--all-pages", "--account-id", "a", "--api-token", "t"]
        )
        with patch.object(cloudcrawl, "build_client", return_value=MagicMock()):
            with patch.object(cloudcrawl, "fetch_all_records", return_value={"records": []}) as fetch_all:
                with patch("builtins.print"):
                    cloudcrawl.cmd_results(args)
        fetch_all.assert_called_once()


class TestWaitExitStatus(unittest.TestCase):
    """wait must exit non-zero when the job's terminal status isn't 'completed'."""

    def _args(self, fetch_results=False, all_pages=False):
        args = MagicMock()
        args.account_id = "acct"
        args.api_token = "tok"
        args.job_id = "job123"
        args.delay = 0
        args.max_attempts = 3
        args.fetch_results = fetch_results
        args.all_pages = all_pages
        args.cache_ttl = None
        return args

    def test_completed_status_exits_cleanly(self):
        args = self._args()
        client = MagicMock()
        client.get_job.return_value = {"status": "completed"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                cloudcrawl.cmd_wait(args)

    def test_errored_status_raises_system_exit(self):
        args = self._args()
        client = MagicMock()
        client.get_job.return_value = {"status": "errored"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as ctx:
                    cloudcrawl.cmd_wait(args)
        self.assertIn("errored", str(ctx.exception))

    def test_cancelled_status_raises_system_exit(self):
        args = self._args(fetch_results=True)
        client = MagicMock()
        client.get_job.return_value = {"status": "cancelled_due_to_limits"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as ctx:
                    cloudcrawl.cmd_wait(args)
        self.assertIn("cancelled_due_to_limits", str(ctx.exception))


class TestResourceTypes(unittest.TestCase):
    """rejectResourceTypes choices must match Cloudflare's full accepted set."""

    def test_all_18_documented_types_present(self):
        self.assertEqual(len(cloudcrawl.RESOURCE_TYPES), 18)
        for expected in ("prefetch", "signedexchange", "ping", "cspviolationreport", "preflight"):
            self.assertIn(expected, cloudcrawl.RESOURCE_TYPES)

    def test_full_type_set_accepted_by_argparse(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["start", "https://example.com",
             "--reject-resource-types", "preflight", "signedexchange", "prefetch",
             "--account-id", "a", "--api-token", "t"]
        )
        self.assertEqual(
            args.reject_resource_types, ["preflight", "signedexchange", "prefetch"]
        )


class TestFileIOErrors(unittest.TestCase):
    """File I/O failures should be clean SystemExits, never raw tracebacks."""

    def test_load_json_arg_unreadable_file_is_clean_system_exit(self):
        with patch("os.path.isfile", return_value=True):
            with patch("builtins.open", side_effect=PermissionError("denied")):
                with self.assertRaises(SystemExit) as ctx:
                    cloudcrawl.load_json_arg("some/file.json", "--cookies")
        self.assertNotIsInstance(ctx.exception, PermissionError)
        self.assertIn("Could not read", str(ctx.exception))
        self.assertIn("--cookies", str(ctx.exception))

    def test_cmd_results_output_write_error_is_clean_system_exit(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["results", "job1", "-o", "/no/such/dir/out.json",
             "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.get_job.return_value = {"id": "job1"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with self.assertRaises(SystemExit) as ctx:
                cloudcrawl.cmd_results(args)
        self.assertNotIsInstance(ctx.exception, OSError)
        self.assertIn("Could not write", str(ctx.exception))


class TestLimitDepthValidation(unittest.TestCase):
    """--limit/--depth must reject values below Cloudflare's documented minimum of 1."""

    def test_start_limit_zero_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--limit", "0",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_start_depth_negative_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--depth", "-1",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_results_limit_zero_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["results", "job1", "--limit", "0",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_valid_limit_and_depth_accepted(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["start", "https://example.com", "--limit", "50", "--depth", "3",
             "--account-id", "a", "--api-token", "t"]
        )
        self.assertEqual(args.limit, 50)
        self.assertEqual(args.depth, 3)


class TestJobIdValidation(unittest.TestCase):
    """An empty job ID must fail clearly, not silently hit the base crawl URL."""

    def test_status_rejects_empty_job_id(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["status", "", "--account-id", "a", "--api-token", "t"])

    def test_results_rejects_empty_job_id(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["results", "", "--account-id", "a", "--api-token", "t"])

    def test_wait_rejects_empty_job_id(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["wait", "", "--account-id", "a", "--api-token", "t"])

    def test_cancel_rejects_empty_job_id(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["cancel", "", "--account-id", "a", "--api-token", "t"])

    def test_whitespace_only_job_id_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["status", "   ", "--account-id", "a", "--api-token", "t"])

    def test_valid_job_id_still_accepted(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["status", "job-abc-123", "--account-id", "a", "--api-token", "t"]
        )
        self.assertEqual(args.job_id, "job-abc-123")

    def test_client_url_raises_on_empty_job_id_directly(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        with self.assertRaises(SystemExit):
            client._url("")

    def test_client_url_base_for_start_crawl(self):
        client = cloudcrawl.CloudCrawlClient("acct", "tok")
        self.assertTrue(client._url().endswith("/browser-rendering/crawl"))
        self.assertTrue(client._url("job1").endswith("/browser-rendering/crawl/job1"))


class TestCacheTtl(unittest.TestCase):
    """--cache-ttl should be threaded into the GET request params."""

    def test_status_includes_cache_ttl(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["status", "job1", "--cache-ttl", "0", "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.get_job.return_value = {"id": "job1"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                cloudcrawl.cmd_status(args)
        self.assertEqual(client.get_job.call_args.kwargs["params"]["cacheTTL"], 0)

    def test_results_includes_cache_ttl(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["results", "job1", "--cache-ttl", "30", "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.get_job.return_value = {"id": "job1"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                cloudcrawl.cmd_results(args)
        self.assertEqual(client.get_job.call_args.kwargs["params"]["cacheTTL"], 30)

    def test_wait_includes_cache_ttl_in_poll_and_fetch(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["wait", "job1", "--cache-ttl", "0", "--fetch-results",
             "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.get_job.return_value = {"status": "completed"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                cloudcrawl.cmd_wait(args)
        calls = client.get_job.call_args_list
        self.assertEqual(calls[0].kwargs["params"]["cacheTTL"], 0)
        self.assertEqual(calls[1].kwargs["params"]["cacheTTL"], 0)

    def test_cache_ttl_omitted_when_not_passed(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(["status", "job1", "--account-id", "a", "--api-token", "t"])
        client = MagicMock()
        client.get_job.return_value = {"id": "job1"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("builtins.print"):
                cloudcrawl.cmd_status(args)
        self.assertNotIn("cacheTTL", client.get_job.call_args.kwargs["params"])

    def test_negative_cache_ttl_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["status", "job1", "--cache-ttl", "-1", "--account-id", "a", "--api-token", "t"]
            )


class TestMaxAgeModifiedSinceBounds(unittest.TestCase):
    def test_max_age_negative_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--max-age", "-1",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_max_age_over_limit_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--max-age", "604801",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_max_age_valid_boundary_accepted(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["start", "https://example.com", "--max-age", "604800",
             "--account-id", "a", "--api-token", "t"]
        )
        self.assertEqual(args.max_age, 604800)

    def test_modified_since_zero_rejected(self):
        parser = cloudcrawl.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--modified-since", "0",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_modified_since_in_future_rejected(self):
        parser = cloudcrawl.build_parser()
        future_ts = int(time.time()) + 3600
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--modified-since", str(future_ts),
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_modified_since_over_a_year_ago_rejected(self):
        parser = cloudcrawl.build_parser()
        old_ts = int(time.time()) - 400 * 24 * 60 * 60
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["start", "https://example.com", "--modified-since", str(old_ts),
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_modified_since_recent_timestamp_accepted(self):
        parser = cloudcrawl.build_parser()
        recent_ts = int(time.time()) - 3600
        args = parser.parse_args(
            ["start", "https://example.com", "--modified-since", str(recent_ts),
             "--account-id", "a", "--api-token", "t"]
        )
        self.assertEqual(args.modified_since, recent_ts)


class TestAllowParamsAndWaitForTimeout(unittest.TestCase):
    def run_start(self, argv):
        parser = cloudcrawl.build_parser()
        return parser.parse_args(argv)

    def test_allow_resource_types_and_pattern_and_wait_for_timeout_in_payload(self):
        args = self.run_start(
            [
                "start", "https://example.com",
                "--allow-resource-types", "document", "script",
                "--allow-request-pattern", "^.*\\.(html)$",
                "--wait-for-timeout", "2000",
                "--account-id", "a", "--api-token", "t",
            ]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        with patch.object(cloudcrawl, "build_client", return_value=client):
            cloudcrawl.cmd_start(args)
        payload = client.start_crawl.call_args[0][0]
        self.assertEqual(payload["allowResourceTypes"], ["document", "script"])
        self.assertEqual(payload["allowRequestPattern"], ["^.*\\.(html)$"])
        self.assertEqual(payload["waitForTimeout"], 2000)

    def test_wait_for_timeout_over_limit_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_start(
                ["start", "https://example.com", "--wait-for-timeout", "120001",
                 "--account-id", "a", "--api-token", "t"]
            )

    def test_no_render_warns_about_new_advanced_params_too(self):
        args = self.run_start(
            ["start", "https://example.com", "--no-render",
             "--allow-resource-types", "document",
             "--wait-for-timeout", "1000",
             "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        stderr = io.StringIO()
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("sys.stderr", stderr):
                cloudcrawl.cmd_start(args)
        self.assertIn("allowResourceTypes", stderr.getvalue())
        self.assertIn("waitForTimeout", stderr.getvalue())


class TestPaginationCap(unittest.TestCase):
    """--all-pages must never loop forever on a non-progressing cursor."""

    @staticmethod
    def _client(responses):
        client = MagicMock()
        client.get_job.side_effect = lambda job_id, params=None: responses[
            min(client.get_job.call_count - 1, len(responses) - 1)
        ]
        return client

    def test_repeating_cursor_raises_instead_of_looping(self):
        stuck = {"id": "j1", "records": [{"url": "a"}], "cursor": "same"}
        client = self._client([stuck])

        with self.assertRaises(SystemExit) as ctx:
            cloudcrawl.fetch_all_records(client, "j1", {})

        self.assertIn("stopped making progress", str(ctx.exception))
        self.assertEqual(client.get_job.call_count, 2)

    def test_max_pages_caps_a_cycle_of_distinct_cursors(self):
        counter = {"n": 0}

        def fake_get_job(job_id, params=None):
            counter["n"] += 1
            return {"id": "j1", "records": [{"url": counter["n"]}], "cursor": counter["n"]}

        client = MagicMock()
        client.get_job.side_effect = fake_get_job

        with self.assertRaises(SystemExit) as ctx:
            cloudcrawl.fetch_all_records(client, "j1", {}, max_pages=5)

        self.assertIn("--max-pages limit of 5", str(ctx.exception))
        self.assertEqual(client.get_job.call_count, 5)

    def test_normal_finite_crawl_completes(self):
        responses = [
            {"id": "j1", "records": [{"url": "a"}], "cursor": 10},
            {"id": "j1", "records": [{"url": "b"}], "cursor": 20},
            {"id": "j1", "records": [{"url": "c"}]},
        ]
        seen = []

        def fake_get_job(job_id, params=None):
            seen.append(dict(params or {}))
            return responses[len(seen) - 1]

        client = MagicMock()
        client.get_job.side_effect = fake_get_job

        result = cloudcrawl.fetch_all_records(client, "j1", {}, max_pages=3)
        self.assertEqual([r["url"] for r in result["records"]], ["a", "b", "c"])

    def test_default_max_pages_exposed_on_results_and_wait(self):
        parser = cloudcrawl.build_parser()
        base = ["--account-id", "a", "--api-token", "t"]
        for argv in (["results", "job1"] + base, ["wait", "job1"] + base):
            args = parser.parse_args(argv)
            self.assertEqual(args.max_pages, cloudcrawl.DEFAULT_MAX_PAGES)

    def test_max_pages_zero_rejected(self):
        parser = cloudcrawl.build_parser()
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    ["results", "job1", "--all-pages", "--max-pages", "0",
                     "--account-id", "a", "--api-token", "t"]
                )
        self.assertIn("must be >= 1", stderr.getvalue())

    def test_cmd_results_forwards_max_pages(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["results", "job1", "--all-pages", "--max-pages", "7",
             "--account-id", "a", "--api-token", "t"]
        )
        with patch.object(cloudcrawl, "build_client", return_value=MagicMock()):
            with patch.object(cloudcrawl, "fetch_all_records", return_value={"records": []}) as fetch_all:
                with patch("builtins.print"):
                    cloudcrawl.cmd_results(args)
        self.assertEqual(fetch_all.call_args.kwargs["max_pages"], 7)

    def test_cmd_wait_forwards_max_pages(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["wait", "job1", "--fetch-results", "--all-pages", "--max-pages", "3",
             "--delay", "0", "--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.get_job.return_value = {"status": "completed"}
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch.object(cloudcrawl, "fetch_all_records", return_value={"records": []}) as fetch_all:
                with patch("builtins.print"):
                    cloudcrawl.cmd_wait(args)
        self.assertEqual(fetch_all.call_args.kwargs["max_pages"], 3)


class TestPathHandling(unittest.TestCase):
    """File paths are normalized, and confinement is opt-in via env var."""

    def setUp(self):
        self._saved = os.environ.pop(cloudcrawl.FILE_ROOT_ENV, None)
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        os.environ.pop(cloudcrawl.FILE_ROOT_ENV, None)
        if self._saved is not None:
            os.environ[cloudcrawl.FILE_ROOT_ENV] = self._saved
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tilde_is_expanded_not_taken_literally(self):
        resolved = cloudcrawl.resolve_path("~/results.json", "--output")
        self.assertNotIn("~", resolved)
        self.assertEqual(resolved, os.path.join(os.path.expanduser("~"), "results.json"))

    def test_relative_path_becomes_absolute(self):
        self.assertTrue(os.path.isabs(cloudcrawl.resolve_path("out.json", "--output")))

    def test_empty_path_is_a_clean_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cloudcrawl.resolve_path("   ", "--output")
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_unconfined_by_default(self):
        self.assertEqual(cloudcrawl.resolve_path("/tmp/x.json", "--output"), "/tmp/x.json")

    def test_confinement_allows_paths_inside_root(self):
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        inside = os.path.join(self.tmpdir, "nested", "out.json")
        self.assertEqual(cloudcrawl.resolve_path(inside, "--output"), inside)

    def test_confinement_rejects_paths_outside_root(self):
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        with self.assertRaises(SystemExit) as ctx:
            cloudcrawl.resolve_path("/etc/passwd", "--output")
        self.assertIn("outside the directory allowed by", str(ctx.exception))

    def test_confinement_rejects_traversal_out_of_root(self):
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        with self.assertRaises(SystemExit):
            cloudcrawl.resolve_path(
                os.path.join(self.tmpdir, "..", "escaped.json"), "--output"
            )

    def test_confinement_rejects_symlink_pointing_out_of_root(self):
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        link = os.path.join(self.tmpdir, "link")
        os.symlink("/etc", link)
        with self.assertRaises(SystemExit):
            cloudcrawl.resolve_path(os.path.join(link, "passwd"), "--cookies")

    def test_load_json_arg_honours_confinement(self):
        outside = os.path.join(tempfile.mkdtemp(), "cookies.json")
        with open(outside, "w") as f:
            json.dump([{"name": "a"}], f)
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        with self.assertRaises(SystemExit) as ctx:
            cloudcrawl.load_json_arg(outside, "--cookies")
        self.assertIn("outside the directory allowed by", str(ctx.exception))

    def test_load_json_arg_reads_allowed_file(self):
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        path = os.path.join(self.tmpdir, "cookies.json")
        with open(path, "w") as f:
            json.dump([{"name": "session"}], f)
        self.assertEqual(cloudcrawl.load_json_arg(path, "--cookies"), [{"name": "session"}])

    def test_inline_json_is_unaffected_by_confinement(self):
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        self.assertEqual(cloudcrawl.load_json_arg('[{"name": "s"}]', "--cookies"), [{"name": "s"}])

    def test_cmd_results_rejects_output_outside_root_before_calling_api(self):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["results", "job1", "-o", "/etc/cloudcrawl.json",
             "--account-id", "a", "--api-token", "t"]
        )
        os.environ[cloudcrawl.FILE_ROOT_ENV] = self.tmpdir
        client = MagicMock()
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with self.assertRaises(SystemExit):
                cloudcrawl.cmd_results(args)
        client.get_job.assert_not_called()


class TestCredentialTransitWarning(unittest.TestCase):
    """Target-site secrets are forwarded to Cloudflare; say so out loud."""

    def _run_start(self, extra_argv):
        parser = cloudcrawl.build_parser()
        args = parser.parse_args(
            ["start", "https://example.com"] + extra_argv
            + ["--account-id", "a", "--api-token", "t"]
        )
        client = MagicMock()
        client.start_crawl.return_value = "job-1"
        stderr = io.StringIO()
        with patch.object(cloudcrawl, "build_client", return_value=client):
            with patch("sys.stderr", stderr):
                with patch("sys.stdout", io.StringIO()):
                    cloudcrawl.cmd_start(args)
        return stderr.getvalue()

    def test_warns_for_http_auth(self):
        out = self._run_start(["--username", "u", "--password", "p"])
        self.assertIn("--username/--password", out)
        self.assertIn("leave your machine", out)

    def test_warns_for_cookies(self):
        out = self._run_start(["--cookies", '[{"name": "session", "value": "abc"}]'])
        self.assertIn("--cookies", out)

    def test_warns_for_custom_headers(self):
        out = self._run_start(["--header", "X-API-Key=secret"])
        self.assertIn("--header", out)

    def test_warning_never_echoes_the_secret_itself(self):
        out = self._run_start(
            ["--username", "admin", "--password", "hunter2",
             "--header", "X-API-Key=topsecret"]
        )
        self.assertNotIn("hunter2", out)
        self.assertNotIn("topsecret", out)

    def test_no_warning_when_no_credentials_are_sent(self):
        out = self._run_start(["--formats", "markdown"])
        self.assertNotIn("leave your machine", out)


class TestPackaging(unittest.TestCase):
    def test_shebang_present_for_direct_execution(self):
        with open("cloudcrawl.py") as f:
            first_line = f.readline().rstrip("\n")
        self.assertEqual(first_line, "#!/usr/bin/env python3")

    def test_runs_as_a_subprocess(self):
        proc = subprocess.run(
            [sys.executable, "cloudcrawl.py", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertIn(b"CloudCrawl", proc.stdout)

    def test_requirements_pin_floor_version(self):
        with open("requirements.txt") as f:
            content = f.read()
        self.assertIn("requests>=", content)

    def test_requirements_exact_floor(self):
        with open("requirements.txt") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
        self.assertEqual(lines, ["requests>=2.32.0"])

    def test_client_class_exposed(self):
        self.assertTrue(hasattr(cloudcrawl, "CloudCrawlClient"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
