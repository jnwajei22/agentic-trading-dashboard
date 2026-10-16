import pytest

from app.brokers.tradelocker.mapping import TradeLockerMappingError, map_configured_array


FIELDS = [
    "balance", "projectedBalance", "availableFunds", "blockedBalance", "cashBalance",
    "unsettledCash", "withdrawalAvailable", "stocksValue", "optionValue",
    "initialMarginReq", "maintMarginReq", "marginWarningLevel", "blockedForStocks",
    "stockOrdersReq", "stopOutLevel", "warningMarginReq", "marginBeforeWarning",
    "todayGross", "todayNet", "todayFees", "todayVolume", "todayTradesCount",
    "openGrossPnL", "openNetPnL", "positionsCount", "ordersCount",
]
VALUES = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 100.0, 0, 0, 166.67, 0, 0, 0, 0, 0, 0.0, 0, 0, 0.0, 0, 0]


def config(fields=FIELDS):
    return {"d": {"accountDetailsConfig": {"columns": [{"id": name} for name in fields]}}}


def state(values=VALUES):
    return {"s": "ok", "d": {"accountDetailsData": values}}


def test_maps_provided_26_value_account_array_and_preserves_zero():
    mapped = map_configured_array(
        config_response=config(), data_response=state(),
        config_key="accountDetailsConfig", data_key="accountDetailsData",
    )
    assert mapped["balance"] == 0
    assert mapped["marginWarningLevel"] == 100.0
    assert mapped["stopOutLevel"] == 166.67


def test_column_order_is_authoritative():
    fields = list(reversed(FIELDS))
    values_by_name = dict(zip(FIELDS, VALUES, strict=True))
    values = [values_by_name[name] for name in fields]
    mapped = map_configured_array(
        config_response=config(fields), data_response=state(values),
        config_key="accountDetailsConfig", data_key="accountDetailsData",
    )
    assert mapped["balance"] == 0 and mapped["stopOutLevel"] == 166.67


@pytest.mark.parametrize("values", [VALUES + [1], VALUES[:-1]])
def test_field_count_mismatch_is_controlled(values):
    with pytest.raises(TradeLockerMappingError) as caught:
        map_configured_array(
            config_response=config(), data_response=state(values),
            config_key="accountDetailsConfig", data_key="accountDetailsData",
        )
    assert caught.value.mismatch is True


@pytest.mark.parametrize(
    ("config_response", "data_response"),
    [
        ({"d": {}}, state()),
        (config(), {"d": {}}),
    ],
)
def test_missing_config_or_data_is_controlled(config_response, data_response):
    with pytest.raises(TradeLockerMappingError, match="Malformed TradeLocker payload"):
        map_configured_array(
            config_response=config_response, data_response=data_response,
            config_key="accountDetailsConfig", data_key="accountDetailsData",
        )
