# Validation record

## Version 0.2.0 — settings verification

Validated on Windows with Python 3.13 on 2026-10-06. Version 0.2.0 adds studio model settings, a short connection test and an apply-settings workflow.

- **87 tests passed** on the final implementation. The suite includes 15 settings integration tests and a deeply nested upstream JSON regression.
- Real HTTP requests to local scripted responders verified settings → generation and judge requests, Authorization headers, token parameters, single-call connection-test budgets, reset and server isolation. They also verified that settings changes cannot redirect an accepted job or implicitly reuse an existing key at another endpoint.
- Actual browser flow verified the settings dialog, filling credentials, a successful short test, applying settings and automatic live-mode selection, a complete six-candidate run through a local responder, and restoring settings. Changing the draft endpoint visibly cleared its unsubmitted key.
- Shipped JavaScript handler checks verified test/apply/reset, token headers, password clearing, equivalent-URL handling, request waiting, Escape and focus restoration.
- GET responses, job results and exports omit keys; malformed JSON and upstream errors return controlled messages. Port-conflict tests preserve the old app and select a free port for the updated app.

These are software checks using local fixtures. No actual model API was configured, and creativity uplift and human superiority remain unmeasured. The release verification artifact records packaging and remote-download checks separately. Local results do not confirm the cloud Actions run.

## Version 0.1.0 — historical validation

Validated on Windows with Python 3.13 on 2026-10-06.

- Exact documented test command: `python -m unittest discover -s tests -v` — **71 tests passed**.
- CLI demonstration, 12-task/24-mode fixture benchmark, blind-pack export and empty-vote summary completed. Missing votes remained missing and no superiority claim was established.
- Actual local-browser flow: example input, asynchronous run, six displayed candidates, archive plot, assumptions/experiment details, and a downloaded JSON attachment.
- HTTP integration tested JSON model-adapter behavior, provider-reported usage, malformed output, paid failures and credential-safe redirect rejection with local scripted servers.
- Python wheel built without fetching dependencies. Its extracted runtime included static UI and canonical task data; the packaged 12-task fixture benchmark completed.
- Publication input excludes API keys, `.env`, local user paths, generated runs, caches, virtual environments and private operational notes.

These checks validate software behavior. The local HTTP responders and DemoProvider are fixtures, not actual model creativity experiments. No live model credentials were configured, no human creativity trial was performed, and no real-world hypothesis has been independently tested. Creativity uplift and human superiority remain unmeasured.

GitHub Actions runs the same checks on Windows/Linux and Python 3.10/3.13. Consult the actual Actions result for the published commit; local results alone do not confirm a cloud run.
