# tools

One `<tool>.yaml` file per tool, so a pull request for one tool touches one file.

Each file has:

- `tool` and `name`: the tool's id and display name.
- `env`: the environment variables the tool needs. A setup lists its own `env` when it needs more, such as an OpenAI key for an extraction model.
- `setups`: one entry per setup on the board.

Each setup has:

- `class`: `main`, `settings`, or `comparison`.
- `endpoint`: the request the setup sends.
- `settings`: the options that set this setup apart. `output_schema: enrichment` means the setup asks for the benchmark's enrichment fields as structured output. The runner also reads three pacing settings: `max_workers` caps parallel requests, `seconds_between_requests` spaces out request starts, and `domains_per_call` sends several companies in one request.
- `pricing`: `usd` per `unit`, and the `source` of the price. `usd` is null when the tool publishes no price. `observed_usd_per_company` is the average cost per company in the published run. It is given when the number of billed units per company varies.
- `note`: anything else a reader needs to know about the setup.

The prices are the ones the leaderboard shows.

To add or update a tool: edit `tools/<tool>.yaml`, run the offline checks, and open a pull request.
