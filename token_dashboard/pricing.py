"""Pricing table + plan-aware cost formatting."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

from .db import connect


def load_pricing(path: Union[str, Path]) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tier_from_name(model: str) -> Optional[str]:
    m = (model or "").lower()
    for tier in ("opus", "sonnet", "haiku"):
        if tier in m:
            return tier
    for tier in ("gpt-5.6-sol", "gpt-5.4-mini", "gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2", "gpt-5"):
        if m == tier or m.startswith(tier + "-"):
            return tier
    return None


def cost_for(model: str, usage: dict, pricing: dict) -> dict:
    """Return {usd, estimated, breakdown}. usd=None when no tier match."""
    rates = pricing["models"].get(model)
    estimated = False
    if rates is None:
        tier = _tier_from_name(model or "")
        if tier and tier in pricing["tier_fallback"]:
            rates = pricing["tier_fallback"][tier]
            estimated = True
        else:
            return {"usd": None, "estimated": True, "breakdown": {}}
    bd = {
        "input":           usage.get("input_tokens", 0)           * rates["input"]           / 1_000_000,
        "output":          usage.get("output_tokens", 0)          * rates["output"]          / 1_000_000,
        "cache_read":      usage.get("cache_read_tokens", 0)      * rates["cache_read"]      / 1_000_000,
        "cache_create_5m": usage.get("cache_create_5m_tokens", 0) * rates["cache_create_5m"] / 1_000_000,
        "cache_create_1h": usage.get("cache_create_1h_tokens", 0) * rates["cache_create_1h"] / 1_000_000,
    }
    gross_read_savings = usage.get("cache_read_tokens", 0) * (rates["input"] - rates["cache_read"]) / 1_000_000
    write_premium = (
        usage.get("cache_create_5m_tokens", 0) * (rates["cache_create_5m"] - rates["input"])
        + usage.get("cache_create_1h_tokens", 0) * (rates["cache_create_1h"] - rates["input"])
    ) / 1_000_000
    return {
        "usd": round(sum(bd.values()), 6), "estimated": estimated, "breakdown": bd,
        "gross_cache_savings_usd": round(max(0.0, gross_read_savings), 6),
        "net_cache_savings_usd": round(gross_read_savings - write_premium, 6),
    }


def financial_summary(rows: list, pricing: dict) -> dict:
    """Aggregate priced rows without presenting unknown models as free."""
    known_cost = 0.0
    savings = 0.0
    priced_calls = 0
    total_calls = 0
    unknown = []
    savings_partial = False
    for row in rows:
        calls = int(row.get("turns", row.get("model_calls", 0)) or 0)
        total_calls += calls
        cost = cost_for(row.get("model", ""), row, pricing)
        if cost["usd"] is None:
            if row.get("model") not in unknown:
                unknown.append(row.get("model") or "unknown")
            if row.get("cache_read_tokens", 0):
                savings_partial = True
            continue
        known_cost += cost["usd"]
        savings += cost["net_cache_savings_usd"]
        priced_calls += calls
    coverage = 1.0 if total_calls == 0 else priced_calls / total_calls
    return {
        "api_equivalent_usd": round(known_cost, 4) if priced_calls else (0.0 if total_calls == 0 else None),
        "known_cost_usd": round(known_cost, 4),
        "priced_model_calls": priced_calls,
        "total_model_calls": total_calls,
        "pricing_coverage": round(coverage, 4),
        "is_lower_bound": bool(unknown),
        "unknown_models": unknown,
        "cache_savings_usd": round(savings, 4),
        "cache_savings_partial": savings_partial,
    }


def codex_credits_for(model: str, usage: dict, pricing: dict) -> Optional[float]:
    rates = pricing.get("codex_credit_rates", {}).get(model)
    if not rates:
        return None
    value = (
        usage.get("input_tokens", 0) * rates["input"]
        + usage.get("cache_read_tokens", 0) * rates["cache_read"]
        + usage.get("output_tokens", 0) * rates["output"]
    ) / 1_000_000
    return round(value, 4)


def get_plan(db_path: Union[str, Path], default: str = "api", source: str = "claude") -> str:
    from .providers import provider_plan
    return provider_plan(db_path, source, default)


def set_plan(db_path: Union[str, Path], plan: str, source: str = "claude") -> None:
    with connect(db_path) as c:
        c.execute("UPDATE platform_settings SET plan=?, configured=1 WHERE source=?", (plan, source))
        if source == "claude":
            c.execute("INSERT OR REPLACE INTO plan (k, v) VALUES ('plan', ?)", (plan,))
        c.commit()


def format_for_user(api_cost_usd: float, plan: str, pricing: dict) -> dict:
    p = pricing["plans"].get(plan, pricing["plans"]["api"])
    if plan == "api" or p["monthly"] == 0:
        return {"display_usd": api_cost_usd, "subtitle": None, "subscription_usd": None}
    return {
        "display_usd":      api_cost_usd,
        "subtitle":         f"You pay ${p['monthly']}/mo on {p['label']}",
        "subscription_usd": p["monthly"],
    }
