"""1C catalog product types (``tire_models.type_id``).

The 1C ``get_wares`` feed sends passenger tyres, truck tyres and wheels
together, and ``_upsert_wares`` stores all of them in ``tire_models`` /
``tire_products``. Anything reading the catalog picks the types it serves
from here — an allowlist, so a new type 1C starts sending stays out of
tyre search until someone decides where it belongs.

Measured on prod 2026-09-28: 3856 passenger models / 60578 SKU,
748 truck models / 1831 SKU, 1341 wheel models / 6321 SKU.
"""

from __future__ import annotations

PASSENGER_TIRE = "000000001"
TRUCK_TIRE = "000000002"
WHEEL = "566"

#: Types the tyre search and availability lookups may return.
TIRE_SEARCH_TYPES: frozenset[str] = frozenset({PASSENGER_TIRE})
