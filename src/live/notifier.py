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
    alert_opened("Momentum Rider v2", 1, 33.33, 105420.00, 0.000316)
    alert_closed("Momentum Rider v2", 1, 105420.00, 108650.00, 33.33, 37.54, 4.21)
    print("Done -- check your email and texts.")
