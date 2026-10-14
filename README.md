# Bali Watch

A runnable local app for your Brisbane → Bali flight-monitoring agent. Python 3.11+; no packages or API key required for the dashboard. Uses SQLite for durable history, deterministic filtering and price-drop alerts. A local MCP server gives an agent tools to read the brief, submit actual Trip.com observations and generate reports.

## Run

From this folder:

```sh
python app.py serve
```

Open **http://127.0.0.1:8765**. The optional sample observation contains the live Trip.com search checked on 14 October 2026: five offers for 8 November, including four qualifying nonstop flights with cabin baggage. Batik Air OD158 was USD 187. This is a point-in-time observation, not a continuously refreshed feed. It is NOT a complete October–December calendar search.

### In GitHub Codespaces

Run these commands in your Codespaces terminal:

```sh
git fetch origin
git checkout flight-monitor
git pull --ff-only
python app.py serve
```

In **Ports**, forward port **8765**, keep its visibility **Private**, and choose **Open in Browser**. Leave the terminal running. The app automatically accepts this Codespace's forwarded HTTPS origin using GitHub's environment variables. It does not accept arbitrary forwarded hosts. Do not switch the port to Public: the dashboard relies on Codespaces' private access gate.

The dashboard starts empty. To view the saved example search, run `python app.py import examples/initial-trip-result.json` in a second terminal, then click **Refresh dashboard**.

If starting with a clean database, import the provided observation once:

```sh
python app.py import examples/initial-trip-result.json
```

Python must have IANA timezone data for Australia/Brisbane. On Windows, `python -m pip install tzdata` may be needed. All departure/arrival times are local to each airport; schedules use Brisbane time.

## What works

- BNE → DPS, one adult, one-way economy, departure window 14 October–14 December 2026.
- Configurable nonstop and confirmed cabin-baggage filters. Unknown baggage is excluded when confirmation is required. Checked baggage is not required.
- USD/AUD quote selection without unverified currency conversion. Other currencies remain stored but do not enter USD/AUD rankings.
- Latest observed offer per flight/date/baggage/currency; observations older than 24 hours are marked stale.
- Persistent history, threshold crossing and meaningful percentage-drop alerts, duplicate-import protection, new lowest observed fare detection.
- Top-five reports, including observation freshness. Reports rank observed offers, not every possible market fare.
- Downloadable search brief, JSON import, agent CLI, bearer-protected write API, stdio MCP tools, optional scheduled adapter runner.
- Local-only server binding, Host validation, same-origin browser writes, CSRF protection, safe Trip.com links, no remote scripts.

No automatic booking. Alerts stay in the app; external email/push is not configured. The reference's existing ChatGPT scheduled tasks were not inspected or changed.

## Connect an agent

Register the local MCP server with an MCP-capable client. Replace the path with this folder's absolute path:

```json
{
  "mcpServers": {
    "bali-flight-agent": {
      "command": "python",
      "args": ["/absolute/path/bali-flight-agent/mcp_server.py"]
    }
  }
}
```

The stdio process has local database access. Use only in a trusted client. This server exposes the monitor's operations; it does not acquire Trip.com credentials or grant Trip.com search access. The agent needs its own supported Trip.com connection. Tools: `get_search_plan`, `get_flight_dashboard`, `import_trip_result`, `generate_flight_report`.

See **AGENT.md** for the full workflow. CLI alternatives:

```sh
python app.py plan
python app.py import result.json
python app.py state
python app.py report
```

For HTTP agents, set a random `FLIGHT_AGENT_TOKEN` in the server's environment, then use `Authorization: Bearer <token>` on POST `/api/import`, `/api/report`, or `/api/refresh`. GET `/api/plan` and `/api/state` are local reads. Store tokens in your secret manager, not in source. `/api/settings` accepts the complete settings object. The server binds only to localhost; do not expose it publicly without a production authentication and TLS layer.

## Unattended monitoring: connection still required

**Not activated.** The Trip.com plugin callable in this chat is not a public Python API. Sites eligibility discovery and publishing helpers were not available in this environment, so no hosted app, cloud schedule, remote notification service or unattended Trip.com integration has been deployed.

The app provides an adapter boundary for a supported provider or a tool-enabled agent runner. Configure `FLIGHT_PROVIDER_COMMAND` as a JSON array of program arguments, for example `["python", "/absolute/path/your_authorised_adapter.py"]`. This is an administrator-controlled environment setting. The app never accepts provider commands from browser input and never runs them through a shell.

Adapter contract:

1. Read the search plan JSON from stdin.
2. Fetch data using your authorised source/tool connection; respect its limits and credentials.
3. Write an array of actual structured Trip.com-compatible responses to stdout (max 2 MB). Each result must indicate `flight.success: true`, with actual currency, journals, segments and baggage flags. Empty successful searches are valid. Errors must produce a nonzero exit code, not invented or empty success data.
4. Provide a unique `transactionId` or top-level `run_id` and timezone-aware `observed_at` for repeat-safe ingestion. Default observation time is import time.

Then run:

```sh
python app.py refresh
python app.py scheduler
```

Keep the scheduler alive separately from the web server, using your operating system's service manager. Defaults: daily at 08:00 and Fridays at 09:00 Brisbane time, stopping after 14 December. Each job is claimed once per date, including failures, to prevent blind automatic retries. Failed jobs remain visible; an explicit `refresh` is available after repair. A process crash can leave a job marked running; investigate before clearing its job row. Scheduler status is based on recorded executions, not process heartbeat. A configured adapter flag does not prove a healthy connection.

No external delivery is implied by saving an alert or report. Add and test an authorised delivery adapter before relying on off-app notifications.

## Verification

```sh
python -m unittest discover -s tests -v
```

The tests use temporary databases and mocked adapters; no live booking or notification is performed. A live Trip.com read and actual import were verified in the development session. Authenticated HTTP writes, same-origin browser writes and hostile Host/origin rejection passed local tests. Browser visual verification and unattended provider execution remain unverified. All 16 tests passed.

## Files and data

`app.py` — database, validation, HTTP API, runner and scheduling.
`mcp_server.py` — agent tools over stdio.
`web/` — responsive dashboard.
`data/flights.sqlite` — your persistent database; back it up while the app is stopped.
`examples/initial-trip-result.json` — actual search response, not a mock.
`AGENT.md` — repeatable agent workflow.

To deploy elsewhere, copy this folder to a persistent machine and run the server locally, or put it behind a correctly configured authenticated reverse proxy after adapting the Host/origin checks. Do not treat the development server as public production hosting.
