# Outstanding external verification

Things that cannot be verified from this machine, with what is needed to close
them. Each names what was done instead, so nobody mistakes a mocked test for a
live one.

---

## Gemini model IDs (P2-8, Part B)

**Status:** PENDING EXTERNAL VERIFICATION — no `GEMINI_API_KEY` available.

**What is unverified:** whether these two ids are served by the live API.

| Setting | Default |
|---|---|
| `ARCHGUARD_PRIMARY_MODEL` | `gemini-3.6-flash` |
| `ARCHGUARD_FALLBACK_MODEL` | `gemini-3.5-flash-lite` |

They are hardcoded in `archguard/llm/gemini.py`. The comment beside them records
that the *previous* 2.x ids "returned 404 for at least one newly issued API
key", which is why they were changed — but the replacements have not themselves
been confirmed against the API by this project. They are taken from Google's
published model list, not from a response.

Nothing here should be read as evidence that they work. If they are wrong, every
AI call fails with a message that reads like a credential problem.

**What was done instead (Part A, shipped):**

- `gemini.list_available_models()` — `GET {base_url}/models`, returns the ids
  the API offers, or `None` if it could not be asked. Never raises.
- `gemini.verify_configured_models()` — compares the *configured* ids against
  that list and names any that are missing. `checked` distinguishes "we asked
  and they are absent" from "we could not ask", because those have different
  fixes.
- A startup probe behind `ARCHGUARD_VERIFY_LLM_ON_BOOT`, off by default, that
  logs at WARNING naming the missing id.
- `GET /api/v1/capabilities`, and a page that disables the Advisor and
  remediation controls with a stated reason rather than letting someone type a
  question and wait for a failure.
- 22 tests against a mocked `/models`: found, missing, both missing,
  unreachable, no key, overridden ids, and each flag spelling.

**To close this item:**

```bash
export GEMINI_API_KEY=...            # a real key
export ARCHGUARD_VERIFY_LLM_ON_BOOT=1
poetry run python -c "
from archguard.llm import gemini
r = gemini.verify_configured_models()
print(r)
print(gemini.list_available_models())
"
```

Expected: `ModelCheck(checked=True, ok=True, missing=[], ...)`.

If `ok` is `False`, the defaults in `archguard/llm/gemini.py` need changing to
ids the printed list contains — and the comment beside them should record what
was actually observed, as the current one does for the 2.x ids.

Then run one real Advisor call end to end, since a model appearing in `/models`
is necessary but not sufficient — it can still refuse a request.
