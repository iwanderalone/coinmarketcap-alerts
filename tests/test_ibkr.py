import os
import sys
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Database
from providers.ibkr import (
    IbkrPosition,
    normalize_ibkr_symbol,
    parse_ibkr_positions_xml,
    fetch_ibkr_positions,
    fetch_ibkr_statement,
)


def test_normalize_ibkr_symbol():
    assert normalize_ibkr_symbol("AAPL") == "AAPL"
    assert normalize_ibkr_symbol("BRK B") == "BRK-B"
    assert normalize_ibkr_symbol("  bf  b  ") == "BF-B"
    assert normalize_ibkr_symbol("BTC") == "BTC"


def test_parse_ibkr_positions_xml_single_lot():
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <FlexQueryResponse queryName="OpenPositions" type="AF">
        <FlexStatements count="1">
            <FlexStatement accountId="U1234567">
                <OpenPositions>
                    <OpenPosition accountId="U1234567" symbol="AAPL" position="15" costBasisPrice="210.50" assetCategory="STK" currency="USD" />
                    <OpenPosition accountId="U1234567" symbol="NVDA" position="20" costPrice="125.00" assetCategory="STK" currency="USD" />
                    <OpenPosition accountId="U1234567" symbol="BTC" position="0.5" costBasisPrice="60000.00" assetCategory="CRYPTO" currency="USD" />
                </OpenPositions>
            </FlexStatement>
        </FlexStatements>
    </FlexQueryResponse>
    """
    positions = parse_ibkr_positions_xml(sample_xml)
    assert len(positions) == 3

    pos_dict = {p.symbol: p for p in positions}
    assert "AAPL" in pos_dict
    assert pos_dict["AAPL"].quantity == 15.0
    assert pos_dict["AAPL"].cost_price == 210.50
    assert pos_dict["AAPL"].asset_type == "stock"

    assert "NVDA" in pos_dict
    assert pos_dict["NVDA"].quantity == 20.0
    assert pos_dict["NVDA"].cost_price == 125.00
    assert pos_dict["NVDA"].asset_type == "stock"

    assert "BTC" in pos_dict
    assert pos_dict["BTC"].quantity == 0.5
    assert pos_dict["BTC"].cost_price == 60000.00
    assert pos_dict["BTC"].asset_type == "crypto"


def test_parse_ibkr_positions_xml_multi_lot_aggregation():
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <FlexQueryResponse queryName="OpenPositions" type="AF">
        <FlexStatements count="1">
            <FlexStatement accountId="U1234567">
                <OpenPositions>
                    <OpenPosition symbol="AAPL" position="10" costBasisPrice="200.0" assetCategory="STK" />
                    <OpenPosition symbol="AAPL" position="10" costBasisPrice="210.0" assetCategory="STK" />
                    <OpenPosition symbol="BRK B" position="5" costPrice="400.0" assetCategory="STK" />
                    <OpenPosition symbol="ZERO" position="0" costPrice="50.0" assetCategory="STK" />
                    <OpenPosition symbol="SHORT" position="-10" costPrice="100.0" assetCategory="STK" />
                </OpenPositions>
            </FlexStatement>
        </FlexStatements>
    </FlexQueryResponse>
    """
    positions = parse_ibkr_positions_xml(sample_xml)
    assert len(positions) == 2

    pos_dict = {p.symbol: p for p in positions}
    assert "AAPL" in pos_dict
    # 10 @ 200 + 10 @ 210 = 20 @ 205.0
    assert pos_dict["AAPL"].quantity == 20.0
    assert pos_dict["AAPL"].cost_price == 205.0

    # Normalized ticker for BRK B -> BRK-B
    assert "BRK-B" in pos_dict
    assert pos_dict["BRK-B"].quantity == 5.0
    assert pos_dict["BRK-B"].cost_price == 400.0


def test_parse_ibkr_positions_error_xml():
    error_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <FlexStatementResponse timestamp="26 October, 2026">
        <Status>Error</Status>
        <ErrorCode>1012</ErrorCode>
        <ErrorMessage>Token has expired</ErrorMessage>
    </FlexStatementResponse>
    """
    with pytest.raises(ValueError) as excinfo:
        parse_ibkr_positions_xml(error_xml)
    assert "1012" in str(excinfo.value)
    assert "Token has expired" in str(excinfo.value)


@pytest.mark.asyncio
async def test_database_ibkr_sync(tmp_path):
    db_file = tmp_path / "test_ibkr.db"
    test_db = Database(db_file)
    await test_db.init()

    chat_id = 99999

    # Initially no config
    cfg = await test_db.get_ibkr_config(chat_id)
    assert cfg is None

    # Set config
    await test_db.set_ibkr_config(chat_id, "test_token_12345", "test_query_9876")
    cfg = await test_db.get_ibkr_config(chat_id)
    assert cfg is not None
    assert cfg["token"] == "test_token_12345"
    assert cfg["query_id"] == "test_query_9876"

    # Add a manual crypto position
    await test_db.add_portfolio_item(chat_id, "BTC", "crypto", 0.5, 60000.0)

    # Sync IBKR positions (stock positions)
    ibkr_positions = [
        IbkrPosition(symbol="AAPL", asset_type="stock", quantity=15.0, cost_price=210.50),
        IbkrPosition(symbol="NVDA", asset_type="stock", quantity=50.0, cost_price=120.00),
    ]

    synced_count = await test_db.sync_ibkr_portfolio(chat_id, ibkr_positions, replace_stocks=True)
    assert synced_count == 2

    # Check portfolio: should contain 2 stocks AND the manual crypto!
    port = await test_db.get_portfolio(chat_id)
    assert len(port) == 3

    symbols = {item.symbol: item for item in port}
    assert "BTC" in symbols
    assert symbols["BTC"].asset_type == "crypto"
    assert symbols["BTC"].quantity == 0.5

    assert "AAPL" in symbols
    assert symbols["AAPL"].quantity == 15.0
    assert symbols["AAPL"].buy_price == 210.50

    assert "NVDA" in symbols
    assert symbols["NVDA"].quantity == 50.0

    # Sync again with updated AAPL quantity and new MSFT position
    updated_ibkr = [
        IbkrPosition(symbol="AAPL", asset_type="stock", quantity=25.0, cost_price=215.00),
        IbkrPosition(symbol="MSFT", asset_type="stock", quantity=10.0, cost_price=420.00),
    ]
    await test_db.sync_ibkr_portfolio(chat_id, updated_ibkr, replace_stocks=True)

    port2 = await test_db.get_portfolio(chat_id)
    symbols2 = {item.symbol: item for item in port2}
    # NVDA should be removed because it's no longer in IBKR, but BTC is preserved!
    assert "NVDA" not in symbols2
    assert "BTC" in symbols2
    assert "AAPL" in symbols2
    assert symbols2["AAPL"].quantity == 25.0
    assert "MSFT" in symbols2

    # Remove config
    removed = await test_db.remove_ibkr_config(chat_id)
    assert removed is True
    cfg_after = await test_db.get_ibkr_config(chat_id)
    assert cfg_after is None
