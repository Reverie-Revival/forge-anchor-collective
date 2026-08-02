"""
Alert copy for Model 3's blended (Grid Stacker) position events.

Reuses notifier.py's SMTP dispatch (same Gmail relay, same env vars) but with
copy distinct enough that a fill, an add, and a capitulation exit are never
mistaken for each other or for a Model 1 alert.
"""
from src.live.notifier import _dispatch


def _slot_label(fill_number: int) -> str:
    return "Slot 1 (base entry)" if fill_number == 0 else f"Cascade add #{fill_number}"


def alert_blend_order_placed(stream_name: str, model_id: int, fill_number: int,
                             usd_in: float, limit_price: float, qty: float, expiry_at: str) -> None:
    label = _slot_label(fill_number)
    _dispatch(
        email_subject=f"Forge: Blend Order Placed - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"{label} -- limit buy placed @ ${limit_price:,.2f}\n"
            f"BTC: {qty:.6f}\n"
            f"Capital: ${usd_in:.2f}\n"
            f"Expires: {expiry_at}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"{label} ORDER @ ${limit_price:,.0f} | {qty:.6f} BTC | expires {expiry_at}"
        ),
    )


def alert_blend_order_expired(stream_name: str, model_id: int, fill_number: int) -> None:
    label = _slot_label(fill_number)
    _dispatch(
        email_subject=f"Forge: Blend Order Expired - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"{label} order expired unfilled.\n"
        ),
        sms_body=f"Model {model_id} | {stream_name}\n{label} ORDER EXPIRED -- never filled",
    )


def alert_blend_opened(stream_name: str, model_id: int, usd_in: float, fill_price: float, qty: float) -> None:
    _dispatch(
        email_subject=f"Forge: Blend Opened - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"Position OPENED (slot 1) @ ${fill_price:,.2f}\n"
            f"BTC: {qty:.6f}\n"
            f"Capital: ${usd_in:.2f}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"BLEND OPEN ${fill_price:,.0f} | {qty:.6f} BTC | ${usd_in:.2f} in"
        ),
    )


def alert_blend_add_filled(stream_name: str, model_id: int, fill_number: int,
                           usd_in: float, fill_price: float, new_avg_cost: float) -> None:
    _dispatch(
        email_subject=f"Forge: Blend Add #{fill_number} Filled - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"Cascade add #{fill_number} FILLED @ ${fill_price:,.2f}\n"
            f"Capital added: ${usd_in:.2f}\n"
            f"New blended avg cost: ${new_avg_cost:,.2f}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"ADD #{fill_number} @ ${fill_price:,.0f} | avg now ${new_avg_cost:,.0f}"
        ),
    )


def alert_blend_closed(stream_name: str, model_id: int, usd_in: float, usd_out: float,
                       pnl: float, exit_reason: str) -> None:
    sign = "+" if pnl >= 0 else ""
    tag = "CAPITULATION STOP" if exit_reason == "capitulation_stop" else "trailing stop"
    _dispatch(
        email_subject=f"Forge: Blend Closed {sign}${pnl:.2f} ({tag}) - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"Position CLOSED -- {tag}\n"
            f"Cash: ${usd_in:.2f} -> ${usd_out:.2f}\n"
            f"P&L: {sign}${pnl:.2f}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"BLEND CLOSED ({tag}) | ${usd_in:.2f} -> ${usd_out:.2f} | {sign}${pnl:.2f}"
        ),
    )
