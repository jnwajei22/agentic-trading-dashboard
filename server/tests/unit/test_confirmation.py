from app.services.trading.confirmation import expected_confirmation_phrase, is_exact_confirmation

def test_expected_confirmation_phrase():
    assert expected_confirmation_phrase("EUR/USD") == "Submit the EURUSD order."

def test_rejects_vague_confirmation():
    assert not is_exact_confirmation("EUR/USD", "bet")
    assert not is_exact_confirmation("EUR/USD", "go ahead")
    assert not is_exact_confirmation("EUR/USD", "yes")

def test_accepts_exact_confirmation():
    assert is_exact_confirmation("EUR/USD", "Submit the EURUSD order.")

def test_rejects_near_but_not_exact_confirmation():
    assert not is_exact_confirmation("EUR/USD", "Submit the EURUSD order. ")
    assert not is_exact_confirmation("EUR/USD", "submit the EURUSD order.")
