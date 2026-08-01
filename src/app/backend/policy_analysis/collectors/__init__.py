"""Collection-domain clients and shared types."""

from policy_analysis.collectors.base import DiscoveredLink, ExtractedArticle, WebFetchClientError
from policy_analysis.collectors.webfetch import WebFetchClient

__all__ = ["DiscoveredLink", "ExtractedArticle", "WebFetchClient", "WebFetchClientError"]
