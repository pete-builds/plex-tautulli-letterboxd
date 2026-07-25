"""Star ratings: scale conversion, and the attribution guard that gates them.

**The attribution problem, verified live on 2026-07-25.**

Plex stores a personal star rating per account. Neither source lets you ask for
a *specific* user's rating:

* Tautulli ``get_metadata`` ignores ``user_id`` entirely. The same call returned
  ``user_rating='10.0'`` with no user, with ``user_id=0``, and with an unrelated
  user id.
* Plex ``/library/metadata/<rk>`` ignores ``accountID``. Identical ``userRating``
  for ``accountID=1`` and ``accountID=2``.

Both return the rating belonging to whoever owns the **token**. On a shared
server that is the admin, not the person whose history is being exported. In the
real library this was checked against, one film had been watched by both the
admin and a shared user, and the API returns the admin's 10/10 either way.

Writing that into the shared user's Letterboxd diary would be a false statement
in someone's public record. A missing rating is an empty cell; a wrong rating is
a lie. So a rating is emitted only when the event's user provably *is* the token
owner, which is what :class:`RatingPolicy` decides.

**Scale.** Plex uses 0 to 10 (its UI shows five stars, half a star per point),
so ``Rating10`` is the natural target and needs no lossy conversion. Zero means
unrated, not "rated zero".
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def normalize_rating10(raw: object) -> int | None:
    """Convert a source rating onto Letterboxd's 1-10 ``Rating10`` scale.

    Returns None for anything absent, unparseable, or zero. Never returns 0: a
    literal ``0`` in the CSV would import as an actual rating rather than as
    "no rating".
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    value = min(value, 10.0)
    # Plex only ever emits whole numbers here; rounding is a guard, not a
    # routine conversion. Half rounds up, so 7.5 becomes 8.
    rounded = int(math.floor(value + 0.5))
    return max(1, min(10, rounded))


@dataclass(frozen=True, slots=True)
class RatingPolicy:
    """Decides, per row, whether a rating can honestly be attributed.

    ``owner_user_id`` is the user the token belongs to. ``None`` means the token
    belongs to the person being exported by construction, which is true in
    ``plex-oauth`` mode where each visitor authenticates as themselves.
    """

    enabled: bool = False
    owner_user_id: str | None = None

    @property
    def emits_column(self) -> bool:
        """When ratings are off the column is omitted entirely, not left blank."""
        return self.enabled

    def applies_to(self, user_id: str | None) -> bool:
        if not self.enabled:
            return False
        if self.owner_user_id is None:
            return True
        return user_id is not None and str(user_id) == str(self.owner_user_id)


DISABLED = RatingPolicy(enabled=False)
