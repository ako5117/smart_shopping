import pytest

from app.config import load_settings


@pytest.fixture
def env(monkeypatch):
    for k in ("DARAJA_ENV", "PUBLIC_BASE_URL", "CALLBACK_SECRET", "DARAJA_SIM_URL", "CARD_PROVIDER",
              "PAYSTACK_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_callback_secret_is_required(env):
    env.setenv("DARAJA_ENV", "simulator")
    with pytest.raises(ValueError, match="CALLBACK_SECRET is not set"):
        load_settings()


@pytest.mark.parametrize("daraja_env", ["sandbox", "production"])
@pytest.mark.parametrize("url", ["", "http://proxy/pay", "shop.example.co.ke"])
def test_real_daraja_needs_public_https_url(env, daraja_env, url):
    env.setenv("DARAJA_ENV", daraja_env)
    env.setenv("CALLBACK_SECRET", "s3cret")
    env.setenv("PUBLIC_BASE_URL", url)
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL must be this server's public https://"):
        load_settings()


def test_valid_settings(env):
    env.setenv("DARAJA_ENV", "sandbox")
    env.setenv("CALLBACK_SECRET", "s3cret")
    env.setenv("PUBLIC_BASE_URL", "https://shop.example.co.ke/pay")
    s = load_settings()
    assert s.callback_url == "https://shop.example.co.ke/pay/payments/mpesa/callback/s3cret"
    assert s.daraja_base_url == "https://sandbox.safaricom.co.ke"


def test_simulator_allows_internal_callback_url(env):
    env.setenv("DARAJA_ENV", "simulator")
    env.setenv("CALLBACK_SECRET", "s3cret")
    env.setenv("PUBLIC_BASE_URL", "http://proxy/pay")
    env.setenv("DARAJA_SIM_URL", "http://mpesa_sim:8099")
    s = load_settings()
    assert s.daraja_base_url == "http://mpesa_sim:8099"
    assert s.callback_url == "http://proxy/pay/payments/mpesa/callback/s3cret"


def test_unknown_env(env):
    env.setenv("DARAJA_ENV", "live")
    with pytest.raises(ValueError, match="DARAJA_ENV must be one of"):
        load_settings()


def test_card_provider_settings(env):
    env.setenv("DARAJA_ENV", "simulator")
    env.setenv("CALLBACK_SECRET", "s3cret")
    assert load_settings().cards_enabled is False
    env.setenv("CARD_PROVIDER", "simulator")
    s = load_settings()
    assert s.cards_enabled and s.paystack_secret_key == "sk_test_simulator"
    env.setenv("CARD_PROVIDER", "paystack")
    with pytest.raises(ValueError, match="PAYSTACK_SECRET_KEY"):
        load_settings()
    env.setenv("PAYSTACK_SECRET_KEY", "sk_live_123")
    assert load_settings().paystack_secret_key == "sk_live_123"
    env.setenv("CARD_PROVIDER", "stripe")
    with pytest.raises(ValueError, match="CARD_PROVIDER"):
        load_settings()
