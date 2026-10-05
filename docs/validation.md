# Version 0.1.0 validation

Validated on Windows with Python 3.13 on 2026-10-06.

- Exact documented test command: `python -m unittest discover -s tests -v` — **71 tests passed**.
- CLI demonstration, 12-task/24-mode fixture benchmark, blind-pack export and empty-vote summary completed. Missing votes remained missing and no superiority claim was established.
- Actual local-browser flow: example input, asynchronous run, six displayed candidates, archive plot, assumptions/experiment details, and a downloaded JSON attachment.
- HTTP integration tested JSON model-adapter behavior, provider-reported usage, malformed output, paid failures and credential-safe redirect rejection with local scripted servers.
- Python wheel built without fetching dependencies. Its extracted runtime included static UI and canonical task data; the packaged 12-task fixture benchmark completed.
- Publication input excludes API keys, `.env`, local user paths, generated runs, caches, virtual environments and private operational notes.

These checks validate software behavior. The local HTTP responders and DemoProvider are fixtures, not actual model creativity experiments. No live model credentials were configured, no human creativity trial was performed, and no real-world hypothesis has been independently tested. Creativity uplift and human superiority remain unmeasured.

GitHub Actions runs the same checks on Windows/Linux and Python 3.10/3.13. Consult the actual Actions result for the published commit; local results alone do not confirm a cloud run.
