# Bali Watch

A Brisbane ↔ Bali return-flight dashboard with SQLite history, price-drop alerts, reports, MCP database tools and a Playwright browser search runner.

## Your search rules

- One adult, economy; BNE ↔ DPS.
- Departures 15 October–15 December 2026; return 7–10 days later, up to 25 December.
- Nonstop and carry-on included in both directions.
- Exclude fares with checked baggage included on either leg. Unknown baggage stays flagged for verification.
- Compare complete return totals in the same currency; never compare a one-way quote with a return total.

## Current readiness

**43 tests pass**, including seventeen real Chromium tests using synthetic pages. Database ingestion, return-trip filtering, browser navigation/checkpointing and verification stops are tested.

**Live Trip.com extraction is not verified.** The development network proxy returns HTTP 403 on connections to Trip.com. An uploaded live card diagnostic now confirms separate outbound selection cards. The first outbound-to-return transition is implemented and tested against that captured layout; return cards have also been captured and their Select step is tested against the observed text. Fare panels have now been captured; their per-leg weight allowances and checked-inclusive exclusion are supported. The multi-step runner now parses captured outbound, return and fare-package layouts. A complete automatic run against live Trip.com is still pending verification on the user’s Windows browser. It stops on unsupported panels, mismatched totals, unverified dates or access challenges. No automatic searches or notifications have been enabled.

The [browser-agent guide](BROWSER_AGENT.md) describes the exact parser requirements and verification limits.

## Dashboard in Codespaces

Python 3.11+; the dashboard itself needs no third-party packages.

```sh
git fetch origin
git checkout browser-flight-agent
git pull --ff-only
python app.py serve
```

Open port **8765** from the Ports tab and keep it **Private**. On your own computer, open http://127.0.0.1:8765. Leave the server terminal running. Refresh dashboard only reloads saved observations—it does not run a search.

The SQLite database is in `data/flights.sqlite`. Existing history and thresholds are preserved; old settings receive the new return-trip rules. The old one-way example under `examples/` is kept for provenance and is excluded from return results. On Windows, install `tzdata` if Python reports missing timezone data.

## Browser agent

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python browser_agent.py plan
```

Interactive login needs a graphical desktop; normal Codespaces does not provide one. Your existing browser tab's Trip.com login is separate from the agent's profile. For first live verification, use a desktop computer and run both the dashboard and agent there so they share the database:

```sh
python browser_agent.py login
python browser_agent.py scan --headed --max-pairs 1
```

The login command opens a browser. Sign in yourself; do not share your password or the ignored `browser_data/` directory. The single-search command attempts one date pair and stops on unsupported content, login challenges or verification. Do not expand coverage until that first live test succeeds.

A manually started batch can continue with `--resume --max-pairs 10`. Checkpoints advance only after valid imports. A search plan change invalidates its checkpoint. The runner inspects each visible nonstop candidate combination, preserves confirmed baggage exclusions, and does not click the fare-package Select/Book control. Missing flight numbers are labelled explicitly rather than invented. The dashboard displays browser status and incomplete coverage. Only complete rendered cards are captured; no claim is made that every fare variant or flight in Trip.com's inventory is visible.

## Agent tools and imports

The stdio MCP server exposes `get_search_plan`, `get_flight_dashboard`, `import_trip_result` and `generate_flight_report`:

```json
{"mcpServers":{"bali-flight-agent":{"command":"python","args":["/absolute/path/FirstAgent24/mcp_server.py"]}}}
```

The database interface does not grant Trip.com access. An authorised agent can also use:

```sh
python app.py plan
python app.py import result.json
python app.py report
```

Import structured Trip.com responses with `flight.success=true`, `tripType=roundTrip` and two reciprocal journals. Preserve provider currency, complete trip total, actual timezone-aware `observed_at` and transaction/run IDs. Failed or incomplete results must not be disguised as successful empty searches.

The localhost HTTP API allows reads at `/api/state` and `/api/plan`. Agent writes require an environment-provided `FLIGHT_AGENT_TOKEN` and `Authorization: Bearer <token>`. Browser writes use same-origin CSRF protection. The server binds to localhost; Codespaces access must stay Private. Do not publicly expose the development server.

## Scheduling

Browser scanning is manually launched. It is not wired into the existing provider scheduler while live extraction remains unverified. The optional legacy `FLIGHT_PROVIDER_COMMAND` adapter reads a plan from stdin and returns an array of valid structured responses; it needs independent authorised data access. `python app.py scheduler` uses Brisbane time, daily at 08:00 and Fridays at 09:00, until the configured end date. Jobs are claimed once per date, including failures; inspect before retrying. Existing ChatGPT scheduled tasks are separate and unchanged.

Alerts and reports stay in this app; email/push delivery is not connected. No booking actions exist.

## Tests

```sh
python -m unittest discover -s tests -v
node --check web/app.js
```

Browser tests require Playwright and Chromium. CI installs them and runs the same tests. Browser fixtures are synthetic and are not evidence of real Trip.com prices, account login or current DOM compatibility.
