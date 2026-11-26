# Live Trading Setup Guide

## ⚠️ IMPORTANT: Read Before Enabling Live Trading

Live trading involves **real money and real risk**. The bot will execute trades with actual funds. Please ensure you:
- Understand the risks involved
- Have tested thoroughly in simulation mode
- Start with small amounts
- Monitor the bot closely during initial live trading

---

## Current Status

✅ **Simulation Mode Active** - Trading with virtual $10,000  
✅ **Bot Manager Running** - Autonomous trading cycles operational  
✅ **GPT-5 Analysis Working** - Real-time market intelligence  
✅ **Risk Management Active** - Capital floor and loss limits enforced

---

## Switch to Live Trading

### Step 1: Obtain Coinbase Advanced Trade API Credentials

1. Go to https://www.coinbase.com/developer-platform/products/advanced-trade-api
2. Sign in to your Coinbase account
3. Navigate to **API Keys** section
4. Click **"Create API Key"**

**Critical Settings:**
- **Signature Algorithm**: Select **ECDSA (ES256)** ⚠️ NOT Ed25519
- **Permissions**: Enable "View" and "Trade"
- **IP Allowlist**: Add your server's IP for security (optional but recommended)
- **API Key Nickname**: "Trading Bot - Production"

5. Click "Create"
6. **IMMEDIATELY SAVE**:
   - API Key ID (format: `organizations/{org_id}/apiKeys/{key_id}`)
   - Private Key (EC Private Key in PEM format)

⚠️ The private key is shown **only once** and cannot be recovered!

---

### Step 2: Update Environment Configuration

Edit `/app/backend/.env`:

```bash
# Open the file
nano /app/backend/.env
```

**Update these lines:**

```env
# Coinbase Credentials (Replace with your real keys)
COINBASE_API_KEY=organizations/YOUR_ORG_ID/apiKeys/YOUR_KEY_ID
COINBASE_API_SECRET=-----BEGIN EC PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg...
YOUR_ACTUAL_PRIVATE_KEY_CONTENT_HERE...
-----END EC PRIVATE KEY-----

# Market data source (live = real Coinbase prices; simulated = fake prices)
MARKET_DATA_SOURCE=live
```

**Important Notes:**
- Keep the `-----BEGIN EC PRIVATE KEY-----` and `-----END EC PRIVATE KEY-----` lines
- Preserve all newlines in the private key
- Ensure proper indentation
- Save the file (Ctrl+O, Enter, Ctrl+X in nano)

---

### Step 3: Restart Backend Service

```bash
sudo supervisorctl restart backend
```

Wait 5 seconds, then verify the bot started correctly:

```bash
tail -n 50 /var/log/supervisor/backend.err.log
```

Look for:
- ✅ "Coinbase client initialized for live trading"
- ❌ "Running in simulation mode" (should NOT appear if done correctly)

---

### Step 4: Fund Your Account

**Before the bot can trade:**

1. Log into Coinbase
2. Deposit USD to your trading account
3. Recommended starting amount: $500 - $2,000
4. The bot uses **exchange wallet** - funds stay on Coinbase

**Risk Parameters (configured in .env):**
- Capital Floor: 97% (bot stops if equity drops below $9,700 per $10,000)
- Max Daily Loss: 1.5% ($150 per $10,000)
- Max Position Size: 5% of equity per trade

---

### Step 5: Activate Live Trading

1. Go to http://localhost:3000
2. Log in to your account
3. Toggle the bot to "Running"
4. The bot will now execute **real trades** with **real money**

---

## Monitoring Live Trading

### Real-Time Monitoring

**Dashboard Metrics:**
- Total Equity (updates with every trade)
- Active Positions (open trades)
- Total Trades (executed count)
- Current Drawdown (risk indicator)

**AI Analysis Tab:**
- GPT-5 market analysis
- Regime classification
- Confidence scores
- Risk factors

### Backend Logs

```bash
# View live trading activity
tail -f /var/log/supervisor/backend.err.log

# Look for:
# - "Trade executed: [SYMBOL] BUY/SELL $[AMOUNT]"
# - "Trade not approved: [REASON]"
# - Risk validation messages
```

### Check Coinbase Account

1. Log into Coinbase
2. Go to **Orders** section
3. Verify trades appear in Coinbase order history
4. Check your portfolio balance matches dashboard

---

## Safety Features

The bot includes multiple layers of protection:

### 1. Capital Floor Protection
- **Threshold**: 97% of all-time high equity
- **Action**: Trading halts if breached
- **Example**: If max equity was $10,000, trading stops if it drops to $9,700

### 2. Daily Loss Limit
- **Threshold**: 1.5% daily loss
- **Action**: Automatic trading halt
- **Example**: Max $150 loss per day on $10,000 capital

### 3. Position Sizing
- **Max per trade**: 5% of total equity
- **Method**: Kelly Criterion with 0.25x safety factor
- **Example**: Max $500 trade on $10,000 capital

### 4. Confidence Threshold
- **Minimum**: 60% confidence required
- **Source**: GPT-5 analysis
- **Action**: Rejects trades below threshold

### 5. Cost Validation
- **Check**: Slippage and execution costs
- **Action**: Rejects high-cost trades
- **Protection**: Prevents excessive fees

---

## Troubleshooting

### Bot Not Executing Trades

**Check 1: API Credentials**
```bash
grep "COINBASE_API_KEY" /app/backend/.env
grep "MARKET_DATA_SOURCE" /app/backend/.env
```
- Verify API key format is correct
- Ensure MARKET_DATA_SOURCE=live

**Check 2: Backend Logs**
```bash
tail -n 100 /var/log/supervisor/backend.err.log | grep -i error
```
- Look for authentication errors
- Check for API permission errors

**Check 3: Account Balance**
- Verify USD balance in Coinbase
- Ensure sufficient funds for trades
- Check trading permissions enabled

### Authentication Errors

**Error: "Invalid API key format"**
- Solution: Verify key format includes `organizations/{org_id}/apiKeys/{key_id}`

**Error: "Invalid signature"**
- Solution: Ensure ECDSA key was selected, not Ed25519
- Verify private key has proper newlines

**Error: "Insufficient permissions"**
- Solution: Recreate API key with "Trade" permission enabled

### Trades Not Appearing in Dashboard

**Refresh the page** - Dashboard polls every 10 seconds

**Check API endpoint:**
```bash
TOKEN=$(curl -s -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"your@email.com","password":"yourpassword"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X GET "http://localhost:8001/api/trades" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Risk Management Best Practices

### Start Small
- Begin with $500-$1,000
- Increase gradually after successful weeks
- Never risk more than you can afford to lose

### Monitor Closely
- Check dashboard 2-3 times daily
- Review AI analysis reasoning
- Watch for unusual market conditions

### Set Alerts (Optional)
- Consider adding email/SMS notifications for:
  - Large trades (>$100)
  - Daily loss approaching limit
  - Capital floor warnings

### Regular Review
- Weekly: Review P&L and trade quality
- Monthly: Analyze strategy performance
- Quarterly: Assess risk parameter effectiveness

---

## Switching Back to Simulation

To return to simulation mode:

1. Edit `/app/backend/.env`
2. Change `MARKET_DATA_SOURCE=live` to `MARKET_DATA_SOURCE=simulated`
3. Restart backend: `sudo supervisorctl restart backend`
4. Bot will resume using simulated data

---

## Emergency Stop

**To immediately halt all trading:**

1. Click the bot toggle to "Stopped" in dashboard, OR
2. Run: 
```bash
curl -s -X POST http://localhost:8001/api/bot/stop \
  -H "Authorization: Bearer YOUR_TOKEN"
```

The bot will complete any in-progress analysis but will not execute new trades.

---

## Support

**Backend Logs:** `/var/log/supervisor/backend.err.log`  
**Database:** `mongo localhost:27017/trading_bot_db`  
**Documentation:** `/app/README.md`

---

## Disclaimer

This trading bot is provided for educational purposes. Cryptocurrency trading involves substantial risk of loss. Past performance does not guarantee future results. The creators are not responsible for any financial losses incurred through use of this software. Trade at your own risk.

**You are fully responsible for:**
- Monitoring the bot's activity
- Managing risk appropriately
- Complying with tax regulations
- Understanding market risks

---

**Status**: Ready for live trading once Coinbase API credentials are configured ✅
