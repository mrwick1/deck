# deck evals

The evals are a fixed set of tasks for the deck agent. They run against the real model and the real `bare-mcp`. Run them after each `bare` or `deck` change, and compare the results with an older run.

The scripts use only the Python 3.11+ standard library.

## Run

Set the model endpoint and the key in the environment. Never write them in a file.

```sh
export DECK_EVAL_BASE_URL=...   # the OpenAI-compatible endpoint
export DECK_MODEL_KEY=...       # the model key
python3 evals/run.py                    # all tasks; runs cargo build --release first
python3 evals/run.py --skip-public      # only the local fixture tasks
python3 evals/run.py --public-only      # only the public sites
python3 evals/run.py --only login-secret,pagination
python3 evals/run.py --deck target/release/deck   # use this binary, skip the build
```

Optional variables:

| Variable | Default |
| --- | --- |
| `DECK_EVAL_AGENT_MODEL` | `agent_model` in `deck.example.toml` |
| `DECK_EVAL_SMALL_MODEL` | `small_model` in `deck.example.toml` |
| `DECK_EVAL_BARE` | `bare.command` in `deck.example.toml` |

The runner does these steps:

1. It starts the fixture site on `127.0.0.1` at a free port.
2. It makes a temp folder with a deck config, a workspace and a secrets file. The secrets file has mode 0600.
3. It starts deck on a free port, in its own process group.
4. It runs each task in a new chat, through the deck API. It reads the run from the event stream.
5. It stops deck and the fixture site. It kills only its own process group.

`--keep` keeps the temp folder, with `deck.log`. The runner also keeps the folder when it stops on an error.

`--secrets-file PATH` adds the `NAME=value` lines of a file to the run's secrets file. Blank lines and `#` comments are skipped. The runner never prints the values. `--state-dir PATH` sets `state_dir` under `[bare]` in the run's config. Without it, the key is not written.

## Results

Each run writes `evals/results/<timestamp>.jsonl`. Git ignores this folder. Each line is one task:

- `pass`, `reasons`: the result and the failed checks.
- `status`, `error`: the end state of the run (`done`, `failed`, `cancelled` or `timeout`).
- `wall_s`, `steps`, `n_tool_calls`, `tool_calls`: the time, the model calls and the tool calls by name.
- `tool_errors`: tool result lines that start with `error`, `not ready` or `script error`, and the errors of failed calls.
- `answer`: the final assistant text.
- `last_calls`: for a failure, the last 3 tool calls with their arguments and results, cut short.

The runner prints a table and a totals line.

Compare a run with an older run:

```sh
python3 evals/run.py --skip-public --compare evals/results/OLD.jsonl
python3 evals/run.py --compare evals/results/OLD.jsonl evals/results/NEW.jsonl   # no run
```

The diff names the tasks that now pass and the tasks that now fail. It shows the change in time and tool calls for each task.

## Scoring

A task passes when the run ends with `done` and every check in `[task.check]` holds. The runner also fails a task when the login password shows in the answer or in the tool arguments.

| Check | Meaning |
| --- | --- |
| `answer` | A regex that must match the final assistant text. |
| `require_tools` | Tools that the agent must call. |
| `forbid_tools` | Tools that the agent must not call. |
| `trace` | A regex that must match the arguments or the full result of a tool call. |
| `logins_ok` | The least number of good logins on the fixture site. |
| `posted` | `path`, an optional `count` of POSTs, and the `fields` of the last POST to that path. |
| `uploaded` | `path`, `filename` and an optional `text` of an uploaded file. |
| `downloaded` | Only with `make_files = true`. Each of the three made files has a copy with the same SHA-256 under `workspace/downloads/`, at any depth and under any name. |

`{{name}}` in a prompt or a check is a value of the run. In a regex, the runner escapes the value. The values are:

- `base`: the URL of the fixture site.
- `account`, `tracking`, `welcome`, `sku`: random codes that the fixture pages show.
- `upload_text`: the text of `report.txt` in the workspace.
- `run_id`: 6 random hex characters. The runner makes a new one for each run.
- `file_txt`, `file_pdf`, `file_png`: the names of the three files that a `make_files` task gets.

Public sites change. Their checks match the shape of an answer, such as a version or an issue URL, not a live value.

## Add a task

1. Add a `[[task]]` to `tasks.toml` with `id`, `prompt`, `timeout_s` and `[task.check]`. Set `fixture = true` for a task on the fixture site. Set `make_files = true` to write the three files `eval-<run_id>.txt`, `.pdf` and `.png` into `workspace/` before the task runs. The text file and the PDF hold the run id. The PNG is a small gradient image.
2. For a fixture task, add a page to `fixtures/`. Add a route to `fixture_server.py` only when the page needs server logic.
3. Public tasks only read. They never log in, post or buy.
4. Run the task with `--only <id>`. Check that it passes for the right reason.
5. Run `python3 -m unittest` in `evals/`.

Run the fixture site alone with `python3 evals/fixture_server.py`. It prints its URL and the values of the run.

## Private tasks

A task for a private site stays out of the repo. Keep its task file, its secrets file and its browser state in a folder outside the repo. Pass them with `--tasks`, `--secrets-file` and `--state-dir`:

```sh
python3 evals/run.py --tasks ~/private/evals.toml --secrets-file ~/private/secrets.env --state-dir ~/private/bare-state
```

Do not commit these files. Do not put their values in the repo.
