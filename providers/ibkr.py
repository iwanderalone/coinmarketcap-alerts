import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional
import httpx

log = logging.getLogger(__name__)

IBKR_SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
IBKR_GET_STATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; MarketInvestorBot/1.0; +https://github.com/iwanderalone/coinmarketcap-alerts)"


@dataclass
class IbkrPosition:
    symbol: str
    asset_type: str  # "stock" or "crypto"
    quantity: float
    cost_price: float
    currency: str = "USD"
    mark_price: Optional[float] = None


def normalize_ibkr_symbol(raw_symbol: str) -> str:
    """
    Normalize IBKR symbols to standard ticker formats (e.g. BRK B -> BRK-B, BF B -> BF-B)
    so they match Yahoo Finance and quote providers.
    """
    cleaned = raw_symbol.strip().upper()
    cleaned = " ".join(cleaned.split()).replace(" ", "-")
    return cleaned


def parse_ibkr_positions_xml(xml_content: str) -> List[IbkrPosition]:
    """
    Parse IBKR Flex Query XML response and extract aggregated positions.
    Handles both single-position summaries and multi-lot breakdowns, aggregating
    lots by symbol with weighted average cost prices.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML received from IBKR: {exc}")

    # Check for error in response
    if root.tag == "FlexStatementResponse":
        status_elem = root.find("Status")
        status_text = status_elem.text if status_elem is not None else ""
        if status_text.lower() in ("error", "fail", "warn"):
            err_code = root.findtext("ErrorCode", "Unknown")
            err_msg = root.findtext("ErrorMessage", "No error message provided")
            raise ValueError(f"IBKR Error [{err_code}]: {err_msg}")

    # Map symbol -> dict(total_qty, total_cost, asset_type, currency, mark_price)
    aggregated: Dict[str, dict] = {}

    for elem in root.iter("OpenPosition"):
        raw_symbol = elem.attrib.get("symbol")
        if not raw_symbol:
            continue

        symbol = normalize_ibkr_symbol(raw_symbol)

        try:
            position_qty = float(elem.attrib.get("position", 0.0))
        except (ValueError, TypeError):
            continue

        # Skip closed/zero positions
        if abs(position_qty) < 1e-6:
            continue

        # Only long positions for standard spot portfolio
        if position_qty < 0:
            log.info("Skipping short position for symbol %s (qty: %f)", symbol, position_qty)
            continue

        # Determine cost price
        cost_price = None
        for cost_attr in ("costBasisPrice", "costPrice", "openPrice"):
            val = elem.attrib.get(cost_attr)
            if val is not None and val != "":
                try:
                    cost_price = float(val)
                    break
                except (ValueError, TypeError):
                    pass

        # Fallback to costBasisMoney / position if available
        if cost_price is None:
            money_val = elem.attrib.get("costBasisMoney")
            if money_val is not None:
                try:
                    cost_price = float(money_val) / position_qty
                except (ValueError, ZeroDivisionError):
                    pass

        # Mark price (optional)
        mark_price = None
        mark_val = elem.attrib.get("markPrice")
        if mark_val:
            try:
                mark_price = float(mark_val)
            except (ValueError, TypeError):
                pass

        if cost_price is None:
            cost_price = mark_price or 0.0

        # Classify asset type
        raw_category = elem.attrib.get("assetCategory", "").strip().upper()
        if raw_category in ("CRYPTO", "CRYPTOCURRENCY"):
            asset_type = "crypto"
        else:
            asset_type = "stock"

        currency = elem.attrib.get("currency", "USD").strip().upper()

        if symbol not in aggregated:
            aggregated[symbol] = {
                "quantity": 0.0,
                "total_cost": 0.0,
                "asset_type": asset_type,
                "currency": currency,
                "mark_price": mark_price,
            }

        aggregated[symbol]["quantity"] += position_qty
        aggregated[symbol]["total_cost"] += position_qty * cost_price
        if mark_price is not None:
            aggregated[symbol]["mark_price"] = mark_price

    results: List[IbkrPosition] = []
    for symbol, data in aggregated.items():
        qty = data["quantity"]
        if qty > 0:
            avg_cost = data["total_cost"] / qty
            results.append(
                IbkrPosition(
                    symbol=symbol,
                    asset_type=data["asset_type"],
                    quantity=round(qty, 6),
                    cost_price=round(avg_cost, 4),
                    currency=data["currency"],
                    mark_price=data["mark_price"],
                )
            )

    return results


async def fetch_ibkr_statement(
    token: str,
    query_id: str,
    max_retries: int = 10,
    retry_delay: float = 2.0,
    timeout: float = 30.0,
) -> str:
    """
    Fetch IBKR Flex Statement XML via official 2-step Flex Web Service.
    Step 1: Request report generation (SendRequest).
    Step 2: Poll GetStatement until the report is ready.
    """
    token_clean = token.strip()
    query_id_clean = query_id.strip()

    if not token_clean or not query_id_clean:
        raise ValueError("IBKR Flex Token and Query ID must not be empty.")

    headers = {"User-Agent": DEFAULT_USER_AGENT}

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        # Step 1: Send Request
        params = {"t": token_clean, "q": query_id_clean, "v": "3"}
        resp = await client.get(IBKR_SEND_REQUEST_URL, params=params)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        status = root.findtext("Status", "")

        if status.lower() != "success":
            err_code = root.findtext("ErrorCode", "Unknown")
            err_msg = root.findtext("ErrorMessage", "Unknown IBKR error during request")
            if err_code == "1018":
                raise ValueError("IBKR Rate Limit (code 1018): Too many requests. Please wait 2-3 minutes before syncing again.")
            elif err_code == "1012":
                raise ValueError("IBKR Error (code 1012): Flex token has expired. Please generate a new token in IBKR Portal.")
            raise ValueError(f"IBKR SendRequest Error [{err_code}]: {err_msg}")

        ref_code = root.findtext("ReferenceCode")
        if not ref_code:
            raise ValueError("IBKR response did not include a ReferenceCode.")

        # Use statement URL provided in SendRequest response or fallback
        statement_url = root.findtext("Url") or root.findtext("url") or IBKR_GET_STATEMENT_URL

        # Step 2: Poll for statement
        for attempt in range(max_retries):
            # IBKR generation takes a few seconds, wait before check
            await asyncio.sleep(retry_delay)

            poll_params = {"q": ref_code, "t": token_clean, "v": "3"}
            poll_resp = await client.get(statement_url, params=poll_params)
            poll_resp.raise_for_status()

            # Check if still generating (code 1019) or error
            try:
                poll_root = ET.fromstring(poll_resp.text)
                if poll_root.tag == "FlexStatementResponse":
                    p_status = poll_root.findtext("Status", "").lower()
                    err_code = poll_root.findtext("ErrorCode", "")
                    if p_status in ("warn", "fail") or err_code == "1019":
                        log.info("IBKR statement generating (attempt %d/%d)...", attempt + 1, max_retries)
                        continue
                    elif p_status == "error":
                        err_msg = poll_root.findtext("ErrorMessage", "Unknown error")
                        raise ValueError(f"IBKR GetStatement Error [{err_code}]: {err_msg}")

                # If tag is FlexQueryResponse, statement is ready
                return poll_resp.text

            except ET.ParseError:
                return poll_resp.text

        raise TimeoutError("IBKR statement generation timed out. Please try syncing again in a minute.")


async def fetch_ibkr_positions(token: str, query_id: str) -> List[IbkrPosition]:
    """Fetch and parse all open positions from Interactive Brokers."""
    xml_text = await fetch_ibkr_statement(token, query_id)
    return parse_ibkr_positions_xml(xml_text)
