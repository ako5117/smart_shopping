# M-Pesa simulator

Imitates the parts of Safaricom Daraja the Payments Service uses (token, STK Push, STK query, callback), so the full checkout can be demoed without a Daraja account or real money. It is **not** Safaricom and never moves money.

```bash
cd tools/mpesa_simulator
uvicorn sim:app --port 8099
```

Run the Payments Service with `DARAJA_ENV=simulator` (and `DARAJA_SIM_URL` if not on `http://localhost:8099`).

The outcome depends on the phone number's last digits: `...000` cancels, `...111` fails with insufficient funds, anything else pays after 3 seconds (`SIM_DELAY_S`).
