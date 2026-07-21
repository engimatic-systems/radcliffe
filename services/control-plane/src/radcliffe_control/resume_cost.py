from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PricingTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    up_to_input_tokens: int | None = Field(default=None, gt=0)
    uncached_input_per_million: Decimal = Field(ge=0)
    cache_write_5m_per_million: Decimal = Field(ge=0)
    cache_read_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    promotional_expires_on: date | None = None


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    model_id: str = Field(min_length=1, max_length=128)
    currency: Literal["USD"]
    pricing_effective_date: date
    pricing_expires_on: date | None
    local: bool
    cold_input_class: Literal["uncached_input", "cache_write_5m"]
    tiers: tuple[PricingTier, ...]

    @model_validator(mode="after")
    def validate_tiers(self) -> ModelPricing:
        if self.local:
            if self.tiers:
                raise ValueError("local model pricing must not contain token rates")
            return self
        if not self.tiers:
            raise ValueError("cloud model pricing requires at least one tier")
        limits = [tier.up_to_input_tokens for tier in self.tiers]
        bounded = [limit for limit in limits if limit is not None]
        if bounded != sorted(set(bounded)) or None in limits[:-1]:
            raise ValueError(
                "pricing tiers require unique ascending bounds and at most one final open tier"
            )
        return self


class PricingDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["radcliffe-model-pricing-v1"] = Field(alias="schema")
    updated_at: date
    source_url: str = Field(pattern=r"^https://")
    models: tuple[ModelPricing, ...]

    @model_validator(mode="after")
    def unique_models(self) -> PricingDocument:
        keys = [(model.provider, model.model_id) for model in self.models]
        if len(keys) != len(set(keys)):
            raise ValueError("pricing registry contains duplicate provider/model entries")
        return self


class ResumeCostEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    no_api_charge: bool
    provider: str | None
    model_id: str
    currency: Literal["USD"]
    minimum_usd: float | None
    maximum_usd: float | None
    projected_input_tokens: int
    representative_output_tokens: int
    token_basis: Literal["exact_projected_payload", "estimated_projected_payload"]
    cache_assumption: Literal["cold", "warm"]
    pricing_basis: str | None
    pricing_effective_date: date | None
    pricing_expires_on: date | None
    input_rate_per_million: float | None
    output_rate_per_million: float | None
    unavailable_reason: (
        Literal[
            "context_overflow",
            "unknown_model",
            "pricing_not_yet_effective",
            "pricing_expired",
            "pricing_tier_unavailable",
            "promotion_expired",
        ]
        | None
    )


class ModelPricingRegistry:
    def __init__(self, document: PricingDocument) -> None:
        self.document = document
        self._by_model = {model.model_id: model for model in document.models}

    @classmethod
    def load(cls, path: Path) -> ModelPricingRegistry:
        document = PricingDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
        return cls(document)

    def estimate(
        self,
        *,
        model_id: str,
        projected_input_tokens: int,
        representative_output_tokens: int,
        exact: bool,
        overflow: bool,
        cache_assumption: Literal["cold", "warm"] = "cold",
        as_of: date | None = None,
    ) -> ResumeCostEstimate:
        if projected_input_tokens < 0 or representative_output_tokens < 0:
            raise ValueError("resume cost token counts must be non-negative")
        today = as_of or date.today()
        pricing = self._by_model.get(model_id)
        common = {
            "model_id": model_id,
            "currency": "USD",
            "projected_input_tokens": projected_input_tokens,
            "representative_output_tokens": representative_output_tokens,
            "token_basis": ("exact_projected_payload" if exact else "estimated_projected_payload"),
            "cache_assumption": cache_assumption,
        }
        if overflow:
            return self._unavailable(common, pricing, "context_overflow")
        if pricing is None:
            return self._unavailable(common, None, "unknown_model")
        if today < pricing.pricing_effective_date:
            return self._unavailable(common, pricing, "pricing_not_yet_effective")
        if pricing.pricing_expires_on is not None and today > pricing.pricing_expires_on:
            return self._unavailable(common, pricing, "pricing_expired")
        basis = self._basis(pricing)
        if pricing.local:
            return ResumeCostEstimate.model_validate(
                {
                    **common,
                    "available": True,
                    "no_api_charge": True,
                    "provider": pricing.provider,
                    "minimum_usd": 0.0,
                    "maximum_usd": 0.0,
                    "pricing_basis": basis,
                    "pricing_effective_date": pricing.pricing_effective_date,
                    "pricing_expires_on": pricing.pricing_expires_on,
                    "input_rate_per_million": 0.0,
                    "output_rate_per_million": 0.0,
                    "unavailable_reason": None,
                }
            )
        tier = next(
            (
                candidate
                for candidate in pricing.tiers
                if candidate.up_to_input_tokens is None
                or projected_input_tokens <= candidate.up_to_input_tokens
            ),
            None,
        )
        if tier is None:
            return self._unavailable(common, pricing, "pricing_tier_unavailable")
        if tier.promotional_expires_on is not None and today > tier.promotional_expires_on:
            return self._unavailable(common, pricing, "promotion_expired")
        input_rate = (
            tier.cache_read_per_million
            if cache_assumption == "warm"
            else tier.cache_write_5m_per_million
            if pricing.cold_input_class == "cache_write_5m"
            else tier.uncached_input_per_million
        )
        input_cost = Decimal(projected_input_tokens) * input_rate / Decimal(1_000_000)
        output_cost = (
            Decimal(representative_output_tokens) * tier.output_per_million / Decimal(1_000_000)
        )
        return ResumeCostEstimate.model_validate(
            {
                **common,
                "available": True,
                "no_api_charge": False,
                "provider": pricing.provider,
                "minimum_usd": float(input_cost),
                "maximum_usd": float(input_cost + output_cost),
                "pricing_basis": basis,
                "pricing_effective_date": pricing.pricing_effective_date,
                "pricing_expires_on": pricing.pricing_expires_on,
                "input_rate_per_million": float(input_rate),
                "output_rate_per_million": float(tier.output_per_million),
                "unavailable_reason": None,
            }
        )

    def _basis(self, pricing: ModelPricing) -> str:
        return (
            f"{pricing.provider}/{pricing.model_id}/"
            f"{pricing.currency.lower()}-{pricing.pricing_effective_date.isoformat()}/"
            f"{self.document.schema_version}"
        )

    def _unavailable(
        self,
        common: dict[str, object],
        pricing: ModelPricing | None,
        reason: str,
    ) -> ResumeCostEstimate:
        return ResumeCostEstimate.model_validate(
            {
                **common,
                "available": False,
                "no_api_charge": False,
                "provider": pricing.provider if pricing else None,
                "minimum_usd": None,
                "maximum_usd": None,
                "pricing_basis": self._basis(pricing) if pricing else None,
                "pricing_effective_date": pricing.pricing_effective_date if pricing else None,
                "pricing_expires_on": pricing.pricing_expires_on if pricing else None,
                "input_rate_per_million": None,
                "output_rate_per_million": None,
                "unavailable_reason": reason,
            }
        )
