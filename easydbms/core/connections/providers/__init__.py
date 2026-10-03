"""Hosted-database plugins: AWS RDS / Aurora, Google Cloud SQL, Azure (token based) and
Supabase, Neon, PlanetScale, CockroachDB Cloud (templates)."""

from __future__ import annotations

from ...dialects import DialectId
from ..models import ProviderKind
from .aws import AwsRds
from .azure import AzureEntra
from .base import (
    Defaults,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderField,
    ProviderUnavailable,
    Token,
)
from .gcp import GoogleCloudSql
from .templates import CockroachCloud, Neon, PlanetScale, Supabase

_PROVIDERS: tuple[Provider, ...] = (
    AwsRds(),
    GoogleCloudSql(),
    AzureEntra(),
    Supabase(),
    Neon(),
    PlanetScale(),
    CockroachCloud(),
)
_BY_KIND = {provider.kind: provider for provider in _PROVIDERS}


def all_providers() -> tuple[Provider, ...]:
    return _PROVIDERS


def get_provider(kind: ProviderKind | str) -> Provider:
    return _BY_KIND[ProviderKind(kind)]


def providers_for(dialect: DialectId) -> list[Provider]:
    return [p for p in _PROVIDERS if dialect in p.dialects]


__all__ = [
    "AwsRds",
    "AzureEntra",
    "CockroachCloud",
    "Defaults",
    "GoogleCloudSql",
    "Neon",
    "PlanetScale",
    "Provider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderField",
    "ProviderUnavailable",
    "Supabase",
    "Token",
    "all_providers",
    "get_provider",
    "providers_for",
]
