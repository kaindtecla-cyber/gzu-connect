"""Minimal Paynow Zimbabwe client for EcoCash / OneMoney express checkout.

Follows Paynow's published API: https://developers.paynow.co.zw/
Every message is signed with SHA512(concatenated values + integration key), upper-case hex.
Only the standard library is used, so there is nothing extra to install.
"""
import hashlib
import hmac
import logging
import urllib.parse
import urllib.request

from django.conf import settings

log = logging.getLogger(__name__)

INITIATE_MOBILE_URL = "https://www.paynow.co.zw/interface/remotetransaction"
ALLOWED_POLL_PREFIX = "https://www.paynow.co.zw/"


class PaynowError(Exception):
    pass


def make_hash(pairs, key):
    raw = "".join(str(v) for k, v in pairs if k.lower() != "hash") + key
    return hashlib.sha512(raw.encode("utf-8")).hexdigest().upper()


def verify(pairs, key):
    got = dict((k.lower(), v) for k, v in pairs).get("hash", "")
    return bool(got) and hmac.compare_digest(got.upper(), make_hash(pairs, key))


def parse(body):
    return urllib.parse.parse_qsl(body, keep_blank_values=True)


def _post(url, pairs):
    data = urllib.parse.urlencode(pairs).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (fixed https URLs only)
            return resp.read().decode("utf-8")
    except Exception as exc:  # network down, timeout, bad status...
        log.warning("Paynow request failed: %s", exc)
        raise PaynowError("Could not reach the payment service. Please try again.") from exc


def _signed_reply(body):
    pairs = parse(body)
    if not verify(pairs, settings.PAYNOW_KEY):
        raise PaynowError("The payment service sent an invalid response.")
    return {k.lower(): v for k, v in pairs}


def initiate_mobile(reference, amount, phone, method, email, result_url, return_url, info="GZU Connect wallet top-up"):
    """Ask Paynow to push a PIN prompt to the customer's phone. Returns poll_url and instructions."""
    pairs = [("id", settings.PAYNOW_ID), ("reference", reference), ("amount", f"{amount:.2f}"),
             ("additionalinfo", info), ("returnurl", return_url), ("resulturl", result_url),
             ("authemail", email), ("phone", phone), ("method", method), ("status", "Message")]
    pairs.append(("hash", make_hash(pairs, settings.PAYNOW_KEY)))
    body = _post(INITIATE_MOBILE_URL, pairs)
    fields = {k.lower(): v for k, v in parse(body)}
    if fields.get("status", "").lower() != "ok":
        # Error replies are not always signed; show Paynow's message but never trust it for money.
        raise PaynowError(fields.get("error") or "Paynow rejected the payment request.")
    fields = _signed_reply(body)
    return {"poll_url": fields.get("pollurl", ""), "instructions": fields.get("instructions", ""),
            "paynow_reference": fields.get("paynowreference", "")}


def poll(poll_url):
    if not poll_url.startswith(ALLOWED_POLL_PREFIX):
        raise PaynowError("Unexpected poll address.")
    return _signed_reply(_post(poll_url, []))
