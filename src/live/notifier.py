import logging
import os
import smtplib
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


def _send(to: str, subject: str, body: str, from_addr: str, password: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_addr, password)
        smtp.sendmail(from_addr, [to], msg.as_string())


def _dispatch(email_subject: str, email_body: str, sms_body: str) -> None:
    from_addr = os.getenv("ALERT_FROM_EMAIL", "").strip()
    password  = os.getenv("ALERT_APP_PASSWORD", "").strip()
    to_email  = os.getenv("ALERT_TO_EMAIL", "").strip()
    to_sms    = os.getenv("ALERT_TO_SMS", "").strip()

    if not all([from_addr, password, to_email]):
        log.debug("Alerting not configured -- skipping")
        return

    try:
        if to_email:
            _send(to_email, email_subject, email_body, from_addr, password)
        if to_sms:
            _send(to_sms, "Forge", sms_body, from_addr, password)
        log.info(f"Alert sent: {email_subject}")
    except Exception as e:
        log.error(f"Alert failed: {e}")


def alert_order_placed(stream_name: str, model_id: int, usd_in: float, limit_price: float, qty: float, expiry_at: str) -> None:
    _dispatch(
        email_subject=f"Forge: Order Placed - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"Limit buy placed @ ${limit_price:,.2f}\n"
            f"BTC: {qty:.6f}\n"
            f"Capital: ${usd_in:.2f}\n"
            f"Expires: {expiry_at}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"ORDER @ ${limit_price:,.0f} | {qty:.6f} BTC | expires {expiry_at}"
        ),
    )


def alert_order_failed(stream_name: str, model_id: int, attempted_usd: float, error: str) -> None:
    """Fires when a real Kraken order placement raises -- most likely cause
    once two models share one Kraken account (see the live-model-1 +
    live-model-2 concurrency review): insufficient USD balance because both
    models' streams signaled close together. Previously this only logged an
    error with no alert -- a real signal that fired and then silently didn't
    trade would otherwise go unnoticed until someone happened to compare
    signal history against live.lots."""
    _dispatch(
        email_subject=f"Forge: Order FAILED - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"A signal fired but the real Kraken order failed to place.\n"
            f"Attempted capital: ${attempted_usd:.2f}\n"
            f"Error: {error}\n"
            f"No lot was created -- this entry was simply skipped this cycle. "
            f"If this is an insufficient-funds error, check the shared Kraken "
            f"account balance against all models' combined open exposure."
        ),
        sms_body=f"Model {model_id} | {stream_name}: ORDER FAILED (${attempted_usd:.2f}) -- {error[:80]}",
    )


def alert_order_expired(stream_name: str, model_id: int, limit_price: float) -> None:
    _dispatch(
        email_subject=f"Forge: Order Expired - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"Limit buy expired unfilled @ ${limit_price:,.2f}\n"
            f"Slot is now free."
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"ORDER EXPIRED @ ${limit_price:,.0f} — never filled"
        ),
    )


def alert_system_down(hours: float) -> None:
    _dispatch(
        email_subject=f"Forge: Executor Silent {hours:.1f}h",
        email_body=(
            f"Forge executor has not run in {hours:.1f} hours.\n"
            f"Expected cadence: every 30 minutes.\n"
            f"Check cron-job.org and GitHub Actions for failures."
        ),
        sms_body=f"Forge executor SILENT {hours:.1f}h — check cron-job.org",
    )


def alert_market_data_stale(model_id: int, latest_ts, expected_ts) -> None:
    _dispatch(
        email_subject=f"Forge: Market Data Stale - Model {model_id}",
        email_body=(
            f"Forge | Model {model_id}\n"
            f"market_data did not catch up in time for this tick and the tick was aborted.\n"
            f"Latest candle in market_data: {latest_ts}\n"
            f"Needed data through: {expected_ts}\n"
            f"No signals were checked and no stops were evaluated this cycle.\n"
            f"Check the market_data_updater workflow/cron for failures -- this tick will retry next cycle."
        ),
        sms_body=f"Model {model_id}: market_data STALE, tick aborted -- check market_data cron",
    )


def alert_sentiment_stale(model_id: int, latest_date, days_old) -> None:
    _dispatch(
        email_subject=f"Forge: Sentiment Data Stale - Model {model_id}",
        email_body=(
            f"Forge | Model {model_id}\n"
            f"sentiment_data did not update in time and the tick was aborted.\n"
            f"Latest sentiment_data entry: {latest_date} ({days_old} days old)\n"
            f"No signals were checked and no stops were evaluated this cycle.\n"
            f"Check the market_data_updater workflow/cron's sentiment step for failures -- "
            f"this tick will retry next cycle."
        ),
        sms_body=f"Model {model_id}: sentiment_data STALE, tick aborted -- check market_data cron",
    )


def alert_fee_drift(real_maker: float, real_taker: float, const_maker: float, const_taker: float,
                    tier_volume: float, next_volume: float) -> None:
    _dispatch(
        email_subject="Forge: Fee Tier Drift Detected",
        email_body=(
            f"Kraken's real fee tier no longer matches MAKER_FEE/TAKER_FEE in code.\n\n"
            f"Code assumes:  maker {const_maker*100:.2f}% / taker {const_taker*100:.2f}%\n"
            f"Kraken reports: maker {real_maker*100:.2f}% / taker {real_taker*100:.2f}%\n\n"
            f"30-day volume: ${tier_volume:,.2f} (next tier at ${next_volume:,.2f})\n\n"
            f"Every backtest, P&L estimate, and breakeven-floor calculation is now off "
            f"until MAKER_FEE/TAKER_FEE are updated in src/live/order_manager.py and "
            f"src/backtester/engine.py (and both live branches)."
        ),
        sms_body=(
            f"Forge: FEE DRIFT -- code {const_maker*100:.2f}%/{const_taker*100:.2f}%, "
            f"Kraken now {real_maker*100:.2f}%/{real_taker*100:.2f}%. Update constants."
        ),
    )


def alert_capital_halted(model_id: int, pool_balance: float, hard_floor: float) -> None:
    _dispatch(
        email_subject=f"Forge: Capital Reserve HALTED - Model {model_id}",
        email_body=(
            f"Forge | Model {model_id}\n"
            f"Pooled capital reserve has crossed the hard floor -- every stream's "
            f"proportional share is now below the $10 minimum lot size, so NO stream "
            f"in this model can place another entry.\n\n"
            f"Pool balance: ${pool_balance:.2f}\n"
            f"Hard floor:   ${hard_floor:.2f}\n\n"
            f"This does not self-recover -- nothing is trading, so nothing can "
            f"generate the winning trade needed to lift the pool back above the "
            f"floor. Requires a manual decision: inject capital, or pull the "
            f"underperforming stream/model (docs/decisions/008)."
        ),
        sms_body=(
            f"Forge: Model {model_id} capital reserve HALTED at ${pool_balance:.2f} "
            f"(floor ${hard_floor:.2f}). No stream can trade. Manual action needed."
        ),
    )


def alert_bucket_buy(model_id: int, usd_in: float, price: float, qty: float) -> None:
    _dispatch(
        email_subject=f"Forge: BTC Bucket Buy - Model {model_id}",
        email_body=(
            f"Forge | Model {model_id} | BTC accumulation bucket\n"
            f"Bought the dip @ ${price:,.2f}\n"
            f"BTC: {qty:.8f}\n"
            f"Capital: ${usd_in:.2f}"
        ),
        sms_body=f"Model {model_id} bucket BUY ${price:,.0f} | {qty:.8f} BTC | ${usd_in:.2f} in",
    )


def alert_bucket_recovery(model_id: int, cash_recovered: float, house_money_added: float, price: float) -> None:
    _dispatch(
        email_subject=f"Forge: BTC Bucket Principal Recovered - Model {model_id}",
        email_body=(
            f"Forge | Model {model_id} | BTC accumulation bucket\n"
            f"Cleared its sell premium @ ${price:,.2f} -- sold enough to recover principal.\n"
            f"Cash recovered: ${cash_recovered:.2f} (back into the bucket, waiting for the next dip)\n"
            f"House money added: {house_money_added:.8f} BTC (never sold again)"
        ),
        sms_body=(
            f"Model {model_id} bucket RECOVERED ${cash_recovered:.2f} principal @ ${price:,.0f}, "
            f"+{house_money_added:.8f} BTC house money"
        ),
    )


def alert_opened(stream_name: str, model_id: int, usd_in: float, fill_price: float, qty: float) -> None:
    _dispatch(
        email_subject=f"Forge: Opened - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"BUY filled @ ${fill_price:,.2f}\n"
            f"BTC: {qty:.6f}\n"
            f"Capital: ${usd_in:.2f}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"BUY ${fill_price:,.0f} | {qty:.6f} BTC | ${usd_in:.2f} in"
        ),
    )


def alert_closed(stream_name: str, model_id: int, entry_price: float, exit_price: float,
                 usd_in: float, usd_out: float, pnl: float) -> None:
    sign = "+" if pnl >= 0 else ""
    _dispatch(
        email_subject=f"Forge: Closed {sign}${pnl:.2f} - Model {model_id} | {stream_name}",
        email_body=(
            f"Forge | Model {model_id} | {stream_name}\n"
            f"SELL (trailing stop)\n"
            f"Entry ${entry_price:,.2f} -> Exit ${exit_price:,.2f}\n"
            f"Cash: ${usd_in:.2f} -> ${usd_out:.2f}\n"
            f"P&L: {sign}${pnl:.2f}"
        ),
        sms_body=(
            f"Model {model_id} | {stream_name}\n"
            f"SELL ${exit_price:,.0f} | ${usd_in:.2f} -> ${usd_out:.2f} | {sign}${pnl:.2f}"
        ),
    )


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    print("Sending test alerts...")
    alert_order_placed("Momentum Rider v2", 1, 33.33, 105420.00, 0.000316, "2026-07-08 18:00 UTC")
    alert_opened("Momentum Rider v2", 1, 33.33, 105420.00, 0.000316)
    alert_closed("Momentum Rider v2", 1, 105420.00, 108650.00, 33.33, 37.54, 4.21)
    alert_order_expired("Breakout Scout v2", 1, 105420.00)
    print("Done -- check your email and texts.")
