"""Fetcher abstraction for multi-source content scraping."""

from src.knowledge.fetchers.base import ContentFetcher, create_fetcher

__all__ = ["ContentFetcher", "create_fetcher"]
