# Git hooks

Local git hooks that run before pushing to main. They mirror what CI runs,
so a rejected push locally will also fail in GitHub Actions.

## Install (one-time per clone)

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-push
```

## What runs

- `pre-push`: on push to `main`, runs `tests/test_syntax.py` and
  `tests/test_bots_config.py`. Blocks the push if either fails.

## Bypass (emergencies only)

```bash
git push --no-verify
```

Never bypass on a production fix without running the tests manually first.
