# CloudCrawl

### Cloudflare `/crawl` Python CLI

A lightweight Python CLI wrapper for Cloudflare Browser Rendering's `/crawl` endpoint.

[Cloudflare Browser Rendering](https://developers.cloudflare.com/browser-run/quick-actions/crawl-endpoint/) provides website crawling with support for Markdown, HTML, JSON extraction, browser rendering, pagination, caching, and more.

---

## Features

* Start and manage crawl jobs
* Check crawl status
* Wait for crawl completion
* Fetch crawl results
* Automatically handle pagination
* Cancel running crawls
* Markdown, HTML, and JSON output
* JSON data extraction
* Content Signals support
* Cache and freshness controls
* Cookies and custom HTTP headers
* HTTP authentication
* Browser rendering options
* Resource and request filtering
* Clean API and network error handling
* Bounded pagination with a configurable page cap

---

## Requirements

* Python 3.6+
* `requests`
* Cloudflare account
* Cloudflare API token with Browser Rendering permissions

---

## Installation

Clone the repository:

```bash
git clone https://github.com/deep-sengupta/CloudCrawl.git
cd cloudcrawl
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Make the CLI executable if desired:

```bash
chmod +x cloudcrawl.py
```

---

## Configuration

Set your Cloudflare credentials as environment variables:

```bash
export CLOUDFLARE_ACCOUNT_ID="your-account-id"
export CLOUDFLARE_API_TOKEN="your-api-token"
```

Using environment variables is recommended instead of passing the API token directly through the command line — `--api-token` still works, but the CLI prints a warning to stderr each time it's used, since the value can leak into shell history and process listings (e.g. `ps`).

Credentials can also be passed per-command with `--account-id`, `--api-token`, and `--timeout` (request timeout in seconds, default `30`) if you'd rather not export environment variables.

---

## Basic Usage

### 1. Start a crawl

```bash
./cloudcrawl.py start https://example.com
```

The command returns a crawl **job ID**.

Example:

```text
abc123...
```

---

### 2. Check the crawl status

```bash
./cloudcrawl.py status <job_id>
```

For a lightweight status check:

```bash
./cloudcrawl.py status <job_id> --lightweight
```

---

### 3. Wait for the crawl to finish

```bash
./cloudcrawl.py wait <job_id> --delay 5 --max-attempts 60
```

Fetch the results automatically after completion:

```bash
./cloudcrawl.py wait <job_id> --delay 5 --max-attempts 60 --fetch-results
```

If the job ends in a non-successful state (`errored`, `cancelled_due_to_timeout`, `cancelled_due_to_limits`, `cancelled_by_user`), `wait` exits with a non-zero status — safe to chain with `&&` in scripts, e.g. `./cloudcrawl.py wait <job_id> && ./upload.sh`.

---

### 4. Get crawl results

```bash
./cloudcrawl.py results <job_id>
```

Filter by URL status within the job:

```bash
./cloudcrawl.py results <job_id> --status completed
```

Save the results to a file:

```bash
./cloudcrawl.py results <job_id> -o results.json
```

For large crawls, automatically fetch every page:

```bash
./cloudcrawl.py results <job_id> --all-pages -o results.json
```

---

### 5. Cancel a crawl

```bash
./cloudcrawl.py cancel <job_id>
```

---

## Basic Workflow

```text
        Start Crawl
             |
             v
       Get Job ID
             |
             v
      Check / Wait
             |
             v
        Completed?
          /    \
        No      Yes
        |        |
        v        v
      Wait     Results
```

A typical workflow:

```bash
./cloudcrawl.py start https://example.com
```

```bash
./cloudcrawl.py wait <job_id> --delay 5 --max-attempts 60
```

```bash
./cloudcrawl.py results <job_id> -o results.json
```

---

## Output Formats

CloudCrawl supports the formats provided by the Cloudflare crawl endpoint.

For example:

```bash
./cloudcrawl.py start https://example.com --formats markdown
```

Multiple formats can be requested where supported:

```bash
./cloudcrawl.py start https://example.com --formats markdown html
```

---

## JSON Extraction

JSON extraction requires a JSON prompt or response schema.

Example:

```bash
./cloudcrawl.py start https://shop.example.com/products \
  --formats json \
  --json-prompt "Extract product name, price, and availability"
```

A response schema can also be provided:

```bash
./cloudcrawl.py start https://shop.example.com/products \
  --formats json \
  --json-response-format '{"type":"json_schema","json_schema":{"name":"product","properties":{"name":"string","price":"number","inStock":"boolean"}}}'
```

`--json-response-format` and `--json-custom-ai` accept either inline JSON or a path to a JSON file.

---

## Crawl Options

For larger crawls, you can control the crawl depth, page limit, and domain scope.

Example:

```bash
./cloudcrawl.py start https://example.com/docs \
  --limit 50 \
  --depth 3
```

Include subdomains:

```bash
./cloudcrawl.py start https://example.com/docs \
  --include-subdomains
```

Exclude URL patterns:

```bash
./cloudcrawl.py start https://example.com/docs \
  --exclude-patterns "https://example.com/docs/archive/**"
```

---

## Caching

Control how long cached resources can be reused:

```bash
./cloudcrawl.py start https://example.com/docs \
  --max-age 3600
```

`--max-age` accepts `0`–`604800` seconds (default `86400`).

Only crawl pages modified since a given point in time:

```bash
./cloudcrawl.py start https://example.com/docs \
  --modified-since "$(date -d '-30 days' +%s)"
```

`--modified-since` must be a Unix timestamp greater than `0`, not in the future, and within the last year.

You can also control the cache TTL used when checking status or retrieving results:

```bash
./cloudcrawl.py status <job_id> --cache-ttl 0
```

A cache TTL of `0` forces a fresh request.

---

## Content Signals

CloudCrawl supports Cloudflare's `crawlPurposes` and `contentUse` options. By default, Cloudflare declares all three crawl purposes and the most permissive content-use level (`full`) unless you narrow them.

Example:

```bash
./cloudcrawl.py start https://example.com \
  --crawl-purposes search \
  --content-use reference
```

Available crawl purposes:

```text
search
ai-input
ai-train
```

Available content-use levels:

```text
reference
full
```

---

## Browser Options

Browser-specific options can be used when rendering is enabled.

Example:

```bash
./cloudcrawl.py start https://app.example.com \
  --goto-wait-until networkidle0 \
  --goto-timeout 60000 \
  --wait-for-selector "[data-content-loaded]" \
  --wait-for-timeout 2000
```

Additional browser options include:

```text
--username
--password
--cookies
--header
--goto-wait-until
--goto-timeout
--wait-for-selector
--wait-for-selector-timeout
--wait-for-selector-visible
--wait-for-timeout
--reject-resource-types
--reject-request-pattern
--allow-resource-types
--allow-request-pattern
```

These options only take effect when browser rendering is enabled.

---

## Pagination

Cloudflare can paginate large crawl responses.

Instead of manually following the returned cursor, use:

```bash
./cloudcrawl.py results <job_id> --all-pages -o results.json
```

The same option can be used with:

```bash
./cloudcrawl.py wait <job_id> --fetch-results --all-pages
```

### Pagination safety cap

`--all-pages` will not follow a cursor indefinitely:

* If the API returns a cursor it has already returned, CloudCrawl stops immediately.
* `--max-pages` (default `1000`) caps the total number of pages even when every cursor is distinct.

Both conditions are errors rather than a quiet early return, so a truncated result set cannot be mistaken for a complete one. The message includes the cursor to resume from:

```bash
./cloudcrawl.py results <job_id> --all-pages --max-pages 5000
```

---

## Security Notes

### Target-site credentials are sent to Cloudflare

`--username`/`--password`, `--cookies`, and `--header` are for authenticating to the **site being crawled**, not to Cloudflare. Because Cloudflare's headless browser is the thing making that request, those values travel to Cloudflare as plaintext JSON fields in the request body (over HTTPS) and are handled by a third party.

This is how the Browser Rendering API works and is not something CloudCrawl can avoid. CloudCrawl prints a one-line note to stderr whenever one of those flags is used; the note never echoes the secret itself, so it is safe in logs.

Prefer scoped, short-lived, or read-only credentials here, and rotate anything you would not want held elsewhere.

### Cloudflare API token

Prefer `CLOUDFLARE_API_TOKEN` over `--api-token`; see [Configuration](#configuration). Unlike the credentials above, this token is sent only in the `Authorization` header to Cloudflare's own API.

### File paths

`-o/--output`, `--cookies`, and `--json-response-format` read and write wherever you point them, which is the default.

To confine every read and write to one directory — for example when wrapping CloudCrawl in something that forwards paths from a less-trusted source — set `CLOUDCRAWL_FILE_ROOT`:

```bash
export CLOUDCRAWL_FILE_ROOT="/var/lib/cloudcrawl/sandbox"
```

Paths are resolved with symlinks followed, so `../` traversal and symlinks pointing out of the root are both rejected. Unset means no confinement.

Paths are expanded and normalized regardless of that variable, so `-o ~/results.json` writes to your home directory.

---

## Error Handling

CloudCrawl handles common network and API failures and returns a clean error message instead of exposing a raw Python traceback.

This includes:

* DNS failures
* Connection errors
* Timeouts
* Connection resets
* Cloudflare API errors
* Rate limiting
* Non-JSON responses
* Proxy or WAF errors
* HTTP 5xx responses

---

## Command Reference

| Command   | Purpose                |
| --------- | ---------------------- |
| `start`   | Start a new crawl      |
| `status`  | Check crawl status     |
| `wait`    | Wait for completion    |
| `results` | Fetch crawl results    |
| `cancel`  | Cancel a running crawl |

For the complete list of options:

```bash
./cloudcrawl.py --help
```

For `start` options:

```bash
./cloudcrawl.py start --help
```

---

## Testing

Run the test suite with:

```bash
python3 test_cloudcrawl.py
```

The project uses Python's standard `unittest` and `unittest.mock` modules.
