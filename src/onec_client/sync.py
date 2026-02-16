"""Catalog synchronization service.

Syncs tire catalog from 1C ERP into PostgreSQL and Redis
for fast search_tires and check_availability queries.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.onec_client.client import OneCClient

logger = logging.getLogger(__name__)

# Trading networks to sync
NETWORKS = ("ProKoleso", "Tshina")

# Seasonality normalization: 1C Russian → internal code
SEASON_MAP = {
    "зимняя": "winter",
    "летняя": "summer",
    "всесезонная": "all_season",
}


def _normalize_season(value: str) -> str:
    """Normalize 1C seasonality string to internal code."""
    return SEASON_MAP.get(value.lower().strip(), value)


def _safe_int(value: str | int | None, default: int = 0) -> int:
    """Safely convert string to int."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_bool(value: str | bool | None) -> bool:
    """Safely convert 1C string to bool (empty string = False)."""
    if isinstance(value, bool):
        return value
    if not value or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "да", "yes")


class CatalogSyncService:
    """Synchronizes 1C tire catalog into PostgreSQL and Redis."""

    def __init__(
        self,
        onec_client: OneCClient,
        db_engine: AsyncEngine,
        redis: Redis | None = None,
        stock_cache_ttl: int = 300,
    ) -> None:
        self._onec = onec_client
        self._engine = db_engine
        self._redis = redis
        self._stock_cache_ttl = stock_cache_ttl

    async def full_sync(self) -> None:
        """Full catalog sync: get_wares (all) + get_stock for both networks.

        Runs at application startup.
        """
        logger.info("Starting full catalog sync from 1C")

        # Sync wares (catalog)
        try:
            resp = await self._onec.get_wares_full()
            wares = resp.get("data", [])
            if wares:
                await self._upsert_wares(wares)
                logger.info("Full wares sync: %d models", len(wares))
            else:
                logger.warning("Full wares sync: empty response from 1C")
        except Exception:
            logger.exception("Failed to sync wares from 1C")
            raise

        # Sync stock for all networks
        await self.sync_stock()

        # Sync Nova Poshta reference data
        await self.sync_novapost()

        logger.info("Full catalog sync completed")

    async def incremental_sync(self) -> None:
        """Incremental catalog sync: changed wares + stock for both networks.

        Runs periodically (every N minutes).
        """
        for network in NETWORKS:
            try:
                resp = await self._onec.get_wares_incremental(network)
                wares = resp.get("data", [])
                if wares:
                    await self._upsert_wares(wares)
                    logger.info(
                        "Incremental sync %s: %d models updated",
                        network,
                        len(wares),
                    )
                    # Confirm receipt so 1C doesn't send same data again
                    await self._onec.confirm_wares_receipt(network)
                else:
                    logger.debug("Incremental sync %s: no changes", network)
            except Exception:
                logger.exception("Failed incremental sync for %s", network)

        # Always refresh stock
        await self.sync_stock()

        # Refresh Nova Poshta reference data
        await self.sync_novapost()

    async def sync_stock(self) -> None:
        """Sync stock/prices from 1C for all networks into DB and Redis."""
        for network in NETWORKS:
            try:
                resp = await self._onec.get_stock(network)
                stock_items = resp.get("data", [])
                if stock_items:
                    await self._upsert_stock(network, stock_items)
                    await self._update_redis_stock(network, stock_items)
                    logger.info(
                        "Stock sync %s: %d items", network, len(stock_items)
                    )
                else:
                    logger.debug("Stock sync %s: empty response", network)
            except Exception:
                logger.exception("Failed stock sync for %s", network)

    async def sync_novapost(self) -> None:
        """Sync Nova Poshta cities and branches from 1C into PostgreSQL."""
        # Sync cities first (branches reference them via FK)
        try:
            resp = await self._onec.get_novapost_cities()
            cities = resp.get("data", [])
            if cities:
                await self._upsert_novapost_cities(cities)
                logger.info("Nova Poshta cities sync: %d items", len(cities))
            else:
                logger.debug("Nova Poshta cities sync: empty response")
        except Exception:
            logger.exception("Failed to sync Nova Poshta cities")

        # Sync branches (only working ones)
        try:
            resp = await self._onec.get_novapost_branches()
            all_branches = resp.get("data", [])
            branches = [
                b for b in all_branches
                if b.get("WarehouseStatus") == "Working"
            ]
            if branches:
                await self._upsert_novapost_branches(branches)
                logger.info(
                    "Nova Poshta branches sync: %d working of %d total",
                    len(branches),
                    len(all_branches),
                )
            else:
                logger.debug("Nova Poshta branches sync: empty response")
        except Exception:
            logger.exception("Failed to sync Nova Poshta branches")

    async def _upsert_novapost_cities(self, cities: list[dict[str, Any]]) -> None:
        """UPSERT Nova Poshta cities into novapost_cities."""
        async with self._engine.begin() as conn:
            for city in cities:
                ref = city.get("Ref", "")
                if not ref:
                    continue

                await conn.execute(
                    text("""
                        INSERT INTO novapost_cities (ref, description, description_ru,
                                                     city_id, area_ref, settlement_type,
                                                     is_branch, synced_at)
                        VALUES (:ref, :description, :description_ru,
                                :city_id, :area_ref, :settlement_type,
                                :is_branch, now())
                        ON CONFLICT (ref) DO UPDATE SET
                            description = EXCLUDED.description,
                            description_ru = EXCLUDED.description_ru,
                            city_id = EXCLUDED.city_id,
                            area_ref = EXCLUDED.area_ref,
                            settlement_type = EXCLUDED.settlement_type,
                            is_branch = EXCLUDED.is_branch,
                            synced_at = now()
                    """),
                    {
                        "ref": ref,
                        "description": city.get("Description", ""),
                        "description_ru": city.get("DescriptionRu", ""),
                        "city_id": city.get("CityID", ""),
                        "area_ref": city.get("Area", ""),
                        "settlement_type": city.get("SettlementTypeDescription", ""),
                        "is_branch": _safe_bool(city.get("IsBranch")),
                    },
                )

    async def _upsert_novapost_branches(self, branches: list[dict[str, Any]]) -> None:
        """UPSERT Nova Poshta branches into novapost_branches."""
        async with self._engine.begin() as conn:
            for branch in branches:
                ref = branch.get("Ref", "")
                if not ref:
                    continue

                await conn.execute(
                    text("""
                        INSERT INTO novapost_branches (ref, description, description_ru,
                                                       short_address, city_ref, city_description,
                                                       number, phone, category, warehouse_status,
                                                       latitude, longitude, postal_code,
                                                       max_weight, synced_at)
                        VALUES (:ref, :description, :description_ru,
                                :short_address, :city_ref, :city_description,
                                :number, :phone, :category, :warehouse_status,
                                :latitude, :longitude, :postal_code,
                                :max_weight, now())
                        ON CONFLICT (ref) DO UPDATE SET
                            description = EXCLUDED.description,
                            description_ru = EXCLUDED.description_ru,
                            short_address = EXCLUDED.short_address,
                            city_ref = EXCLUDED.city_ref,
                            city_description = EXCLUDED.city_description,
                            number = EXCLUDED.number,
                            phone = EXCLUDED.phone,
                            category = EXCLUDED.category,
                            warehouse_status = EXCLUDED.warehouse_status,
                            latitude = EXCLUDED.latitude,
                            longitude = EXCLUDED.longitude,
                            postal_code = EXCLUDED.postal_code,
                            max_weight = EXCLUDED.max_weight,
                            synced_at = now()
                    """),
                    {
                        "ref": ref,
                        "description": branch.get("Description", ""),
                        "description_ru": branch.get("DescriptionRu", ""),
                        "short_address": branch.get("ShortAddress", ""),
                        "city_ref": branch.get("CityRef", ""),
                        "city_description": branch.get("CityDescription", ""),
                        "number": branch.get("Number", ""),
                        "phone": branch.get("Phone", ""),
                        "category": branch.get("CategoryOfWarehouse", ""),
                        "warehouse_status": branch.get("WarehouseStatus", ""),
                        "latitude": branch.get("Latitude", ""),
                        "longitude": branch.get("Longitude", ""),
                        "postal_code": branch.get("PostalCodeUA", ""),
                        "max_weight": _safe_int(branch.get("PlaceMaxWeightAllowed")),
                    },
                )

    async def _upsert_wares(self, wares: list[dict[str, Any]]) -> None:
        """UPSERT wares data into tire_models and tire_products."""
        async with self._engine.begin() as conn:
            for ware in wares:
                model_id = ware.get("model_id", "")
                if not model_id:
                    continue

                # Upsert tire_models
                await conn.execute(
                    text("""
                        INSERT INTO tire_models (id, name, manufacturer_id, manufacturer,
                                                 seasonality, tread_pattern_type, type_id, updated_at)
                        VALUES (:id, :name, :manufacturer_id, :manufacturer,
                                :seasonality, :tread_pattern_type, :type_id, now())
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            manufacturer_id = EXCLUDED.manufacturer_id,
                            manufacturer = EXCLUDED.manufacturer,
                            seasonality = EXCLUDED.seasonality,
                            tread_pattern_type = EXCLUDED.tread_pattern_type,
                            type_id = EXCLUDED.type_id,
                            updated_at = now()
                    """),
                    {
                        "id": model_id,
                        "name": ware.get("model", ""),
                        "manufacturer_id": ware.get("manufacturer_id", ""),
                        "manufacturer": ware.get("manufacturer", ""),
                        "seasonality": _normalize_season(ware.get("seasonality", "")),
                        "tread_pattern_type": ware.get("tread_pattern_type", ""),
                        "type_id": ware.get("type", ""),
                    },
                )

                # Upsert tire_products
                for product in ware.get("product", []):
                    sku = product.get("sku", "")
                    if not sku:
                        continue

                    await conn.execute(
                        text("""
                            INSERT INTO tire_products (sku, model_id, diameter, width, profile,
                                                       size, speed_rating, load_rating, studded,
                                                       description, updated_at)
                            VALUES (:sku, :model_id, :diameter, :width, :profile,
                                    :size, :speed_rating, :load_rating, :studded,
                                    :description, now())
                            ON CONFLICT (sku) DO UPDATE SET
                                model_id = EXCLUDED.model_id,
                                diameter = EXCLUDED.diameter,
                                width = EXCLUDED.width,
                                profile = EXCLUDED.profile,
                                size = EXCLUDED.size,
                                speed_rating = EXCLUDED.speed_rating,
                                load_rating = EXCLUDED.load_rating,
                                studded = EXCLUDED.studded,
                                description = EXCLUDED.description,
                                updated_at = now()
                        """),
                        {
                            "sku": sku,
                            "model_id": model_id,
                            "diameter": _safe_int(product.get("diametr")),
                            "width": _safe_int(product.get("profile_width")),
                            "profile": _safe_int(product.get("profile_height")),
                            "size": product.get("size", ""),
                            "speed_rating": product.get("speed_rating", ""),
                            "load_rating": product.get("load_rating", ""),
                            "studded": _safe_bool(product.get("studded")),
                            "description": product.get("text", ""),
                        },
                    )

    async def _upsert_stock(
        self, network: str, stock_items: list[dict[str, Any]]
    ) -> None:
        """UPSERT stock data into tire_stock."""
        async with self._engine.begin() as conn:
            for item in stock_items:
                sku = item.get("sku", "")
                if not sku:
                    continue

                await conn.execute(
                    text("""
                        INSERT INTO tire_stock (sku, trading_network, price, price_tshina,
                                                stock_quantity, country, year_issue, synced_at)
                        VALUES (:sku, :network, :price, :price_tshina,
                                :stock_quantity, :country, :year_issue, now())
                        ON CONFLICT (sku, trading_network) DO UPDATE SET
                            price = EXCLUDED.price,
                            price_tshina = EXCLUDED.price_tshina,
                            stock_quantity = EXCLUDED.stock_quantity,
                            country = EXCLUDED.country,
                            year_issue = EXCLUDED.year_issue,
                            synced_at = now()
                    """),
                    {
                        "sku": sku,
                        "network": network,
                        "price": _safe_int(item.get("price")),
                        "price_tshina": _safe_int(item.get("price_tshina")),
                        "stock_quantity": _safe_int(item.get("stock")),
                        "country": item.get("country", ""),
                        "year_issue": item.get("year_issue", ""),
                    },
                )

    async def _update_redis_stock(
        self, network: str, stock_items: list[dict[str, Any]]
    ) -> None:
        """Update Redis hash with stock data for fast check_availability."""
        if self._redis is None:
            return

        key = f"onec:stock:{network}"
        pipe = self._redis.pipeline()
        for item in stock_items:
            sku = item.get("sku", "")
            if not sku:
                continue
            value = json.dumps(
                {
                    "price": _safe_int(item.get("price")),
                    "stock": _safe_int(item.get("stock")),
                    "country": item.get("country", ""),
                    "year_issue": item.get("year_issue", ""),
                },
                ensure_ascii=False,
            )
            pipe.hset(key, sku, value)
        pipe.expire(key, self._stock_cache_ttl * 2)  # 2x TTL as safety margin
        await pipe.execute()
