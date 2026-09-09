"""Did a ``book_fitting`` result actually reserve a slot?

Wave 17 (2026-09-09). ``ToolRouter.execute`` reports ``success=True`` for any
handler that returns without raising, and every ``book_fitting`` guard rejection
returns a plain ``{"error": True, ...}`` dict. Reading that flag as "booked"
marked the session booked on a *refusal*, which then

* stripped ``book_fitting`` from the toolset (``tools.py``), so the LLM could
  not retry even after collecting the missing field;
* silenced the Wave 15 false-claim detector, which skips a booked session;
* replaced the whole progress block with «✅ ЗАПИС СТВОРЕНО. Далі — тільки
  прощання», so the bot told the caller they were booked *as instructed*.

Calls ``c1988daf`` and ``57494646`` ended exactly that way. The rule below is
therefore default-deny: a positive marker must be present, and an explicit
error outranks any marker that sits beside it.
"""

from __future__ import annotations

from typing import Any


def is_booking_confirmed(result: Any) -> bool:
    """True only when the tool result carries a positive booking marker.

    ``status == "confirmed"`` is the 1C path; a truthy ``id`` is the Store API
    fallback. Anything else — a rejection, a bare message, a non-dict — is not
    a booking.
    """
    if not isinstance(result, dict) or result.get("error"):
        return False
    return result.get("status") == "confirmed" or bool(result.get("id"))
