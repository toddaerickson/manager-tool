"""The only module allowed to construct an Anthropic client or call it.

Every outbound request goes through `PrivateRequest.send`, which:
1. replaces people's names with per-request tokens (coaching/privacy.py),
   in two passes: field by field while the prompt is built (via
   `_sanitize_user_text`, before any length cap can split a name), then
   over the finished message as a backstop;
2. tells the model to keep tokens verbatim;
3. calls the API;
4. puts real names back into the response.

A static test fails if `Anthropic(` or `.messages.` appears anywhere
else in the codebase, so a new feature cannot bypass this.
"""

import os

from coaching import privacy

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

MODEL = "claude-sonnet-4-6"

_TOKEN_INSTRUCTION = (
    "\n\nPRIVACY: Names in the data are replaced by placeholders: the "
    "character ⟦, then P or A, then a number, then ⟧. Refer to people only "
    "by the placeholders that appear in the data, copied character for "
    "character. Never guess or invent a real name, and never create a "
    "placeholder that does not appear in the data."
)


def build_client(manager_id):
    """Anthropic client for this manager, or None (no SDK or no key).
    Per-manager DB key wins; ANTHROPIC_API_KEY env is the fallback."""
    if Anthropic is None:
        return None
    from core.services.email import get_config

    api_key = get_config("anthropic_api_key", manager_id) or os.environ.get(
        "ANTHROPIC_API_KEY"
    )
    if not api_key:
        return None
    return Anthropic(api_key=api_key)


class PrivateRequest:
    """Context manager for building and sending one AI request.

        with PrivateRequest(manager_id) as req:
            msg = build_prompt(...)          # names tokenised as built
            text = req.send(client, SYSTEM, msg, max_tokens=500)
    """

    def __init__(self, manager_id):
        self.manager_id = manager_id
        self.pseudonymizer = privacy.Pseudonymizer(
            privacy.people_for_manager(manager_id)
        )
        self._ctx = None

    def __enter__(self):
        self._ctx = privacy.activate(self.pseudonymizer)
        return self

    def __exit__(self, *exc):
        privacy.deactivate(self._ctx)
        return False

    def send(self, client, system, user_message, max_tokens):
        """Return the model's text with real names restored ("" when the
        response has no content). API errors propagate to the caller."""
        safe_message = self.pseudonymizer.redact(user_message, escape_tokens=False)
        message = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system + _TOKEN_INSTRUCTION,
            messages=[{"role": "user", "content": safe_message}],
        )
        text = message.content[0].text if message.content else ""
        return self.pseudonymizer.restore(text)
