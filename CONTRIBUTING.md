# Contributing

Discuss changes in an issue before expanding the supported input formats. Keep diagnostics
deterministic, avoid network access in the runtime, and include a regression test for fixes.
Use synthetic fixtures; never submit real support bundles or credentials.

```sh
python -m venv .venv
# Activate .venv (Windows: .venv\Scripts\activate; POSIX: . .venv/bin/activate)
python -m pip install -e . ruff==0.17.2 build==1.6.1
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python -m build
```

Submissions must be compatible with the MIT license. Preserve third-party attribution.
CI only tests and builds; it never creates commits. The public API is provisional in v0.1.
