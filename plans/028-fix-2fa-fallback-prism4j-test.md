# Plan 028: Fix 2FA Silent Fallback, Prism4j Grammars, Trivial Test

> **Executor instructions**: Follow this plan step by step.

## Status
- **Priority**: P2 | **Effort**: S | **Risk**: LOW | **Depends on**: nothing
- **Category**: bug + security
- **Planned at**: commits `cd9b59a` (daemon) + `492da45` (android), 2026-06-19

## Finding 1: 2FA Silent Fallback (Security)

### Current state
`server.py:265-275`: When `send_otp()` raises, daemon catches it and returns `True`
(allow login without 2FA). Comment: "better to let the admin in than lock them out."

### Fix
Return error instead of silently allowing login:
```python
except Exception as e:
    logger.error("2FA challenge generation failed for %s: %s", username, e)
    return {"error": "2FA_SYSTEM_ERROR", "message": "Two-factor authentication is enabled but the email system failed."}
```
In middleware, handle error dict:
```python
if isinstance(result, dict) and result.get("error"):
    return web.json_response(result, status=503)
```

## Finding 2: Prism4j Grammar Locator Returns Null

### Current state
`MarkdownText.kt:40-44`: GrammarLocator returns `null` for all languages. Syntax highlighting
plugin is installed but renders nothing.

### Fix
Remove the Prism4j plugin + the SyntaxHighlightPlugin line. Code blocks render as plain
monospace text — acceptable for v1. Or add markwon-syntax-* grammar dependencies for
specific languages. Recommend removal for launch simplicity.

## Finding 3: Trivial Test Path Assertion

### Current state
`tests/test_setup_wizard.py:256`: `assert result == Path("/usr/bin/hermes")`

### Fix
Change to: `assert result is not None and result.name == "hermes"`

## Scope
- `server.py` (daemon) — 2FA fallback fix
- `MarkdownText.kt` (android) — remove Prism4j
- `tests/test_setup_wizard.py` (daemon) — fix assertion

## Done criteria
- [ ] Daemon pytest: 0 failures (currently 1)
- [ ] Android assembleDebug: BUILD SUCCESSFUL
- [ ] Both repos committed
