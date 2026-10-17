"""Deterministic, fee-aware capital recovery and defensive exit sizing.

A price quote is NOT a firm executable bid. Every predicted result is an estimate;
settlement must be reconciled against on-chain SOL and token deltas.
"""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RecoveryPlan:
    fraction: float
    units_to_sell: float
    estimated_net_usd: float
    unrecovered_principal_usd: float
    estimated_remaining_units: float


def check_positive(**numbers: float) -> None:
    if any(not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in numbers.values()):
        raise ValueError('Missing, nonfinite, or nonpositive input: ' + ', '.join(numbers))


def estimate_exit_net_usd(units: float, usd_price: float, liquidity_usd: float, *,
                          sell_fee_fraction: float = .015, maximum_slippage_fraction: float = .0075,
                          network_cost_usd: float = .05, impact_coefficient: float = .5) -> float:
    check_positive(units=units, usd_price=usd_price, liquidity_usd=liquidity_usd)
    if not (0 <= sell_fee_fraction <= .1 and 0 <= maximum_slippage_fraction < .01 and
            0 <= network_cost_usd < 1e5 and 0 <= impact_coefficient <= 5):
        raise ValueError('Invalid trading cost assumptions')
    gross = units * usd_price
    impact = impact_coefficient * gross / liquidity_usd
    if impact > maximum_slippage_fraction:
        raise ValueError('Predicted liquidity impact exceeds allowed slippage; no executable quote')
    return max(0.0, gross * (1 - sell_fee_fraction - maximum_slippage_fraction - impact) - network_cost_usd)


def plan_principal_recovery(*, remaining_units: float, initial_units: float, entry_cost_usd: float,
                            recovered_usd: float, current_usd_price: float, liquidity_usd: float,
                            trigger_multiple: float = 2.0, sell_fee_fraction: float = .015,
                            maximum_slippage_fraction: float = .0075, network_cost_usd: float = .05,
                            min_runner_fraction: float = .05, min_buffer_fraction: float = .02) -> RecoveryPlan | None:
    """Try to sell principal+buffer, leaving a runner. Returns None when not feasible.

    Triggers off remaining position VALUE plus prior proceeds relative to original
    cost. Extra revenue buffer avoids a nominal $5 recovery netting <$5 after fees.
    """
    check_positive(remaining_units=remaining_units, initial_units=initial_units,
                   entry_cost_usd=entry_cost_usd, current_usd_price=current_usd_price,
                   liquidity_usd=liquidity_usd)
    if not (0 <= recovered_usd <= entry_cost_usd * 1000):raise ValueError('Invalid proceeds')
    if not (1 < trigger_multiple <= 1000 and 0 <= min_runner_fraction < 1 and 0 <= min_buffer_fraction <= .25):
        raise ValueError('Invalid recovery policy')
    to_recover = max(0., entry_cost_usd * (1 + min_buffer_fraction) - recovered_usd)
    if to_recover <= 1e-9:return None
    # Trigger at a real observed multiple of *total economic position value*, not
    # an incorrect ratio of entry and DexScreener price alone.
    if (remaining_units * current_usd_price + recovered_usd) / entry_cost_usd < trigger_multiple:
        return None
    max_sale = max(0.0, min(remaining_units, initial_units * (1 - min_runner_fraction)))
    if max_sale <= 0 or max_sale >= remaining_units and min_runner_fraction > 0:return None
    try:
        largest_net = estimate_exit_net_usd(max_sale,current_usd_price,liquidity_usd,
                        sell_fee_fraction=sell_fee_fraction,maximum_slippage_fraction=maximum_slippage_fraction,
                        network_cost_usd=network_cost_usd)
    except ValueError:
        return None
    if largest_net < to_recover:return None
    # A binary search is robust to costs whose price impact grows with quantity.
    low, high = 0., max_sale
    for _ in range(65):
        mid = (low + high)/2
        try:
            net = estimate_exit_net_usd(mid,current_usd_price,liquidity_usd,
                        sell_fee_fraction=sell_fee_fraction,maximum_slippage_fraction=maximum_slippage_fraction,
                        network_cost_usd=network_cost_usd)
        except ValueError:
            high=mid
            continue
        if net >= to_recover:high=mid
        else:low=mid
    qty = high
    net = estimate_exit_net_usd(qty,current_usd_price,liquidity_usd,
                        sell_fee_fraction=sell_fee_fraction,maximum_slippage_fraction=maximum_slippage_fraction,
                        network_cost_usd=network_cost_usd)
    return RecoveryPlan(qty/remaining_units,qty,net,to_recover,remaining_units-qty)


def defensive_exit(*, entry_price: float, current_price: float, high_price: float,
                   hard_stop_fraction: float = .07, trailing_fraction: float = .08,
                   trail_activation_multiple: float = 1.15) -> str | None:
    check_positive(entry_price=entry_price,current_price=current_price,high_price=high_price)
    if not (0 < hard_stop_fraction < 1 and 0 < trailing_fraction < 1 and trail_activation_multiple >= 1):
        raise ValueError('Invalid stop configuration')
    if current_price <= entry_price * (1-hard_stop_fraction):return 'stop_loss'
    if high_price >= entry_price*trail_activation_multiple and current_price <= high_price*(1-trailing_fraction):
        return 'trailing_profit_protection'
    return None
