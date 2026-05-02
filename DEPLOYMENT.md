# 🚀 Vera Bot — Deployment Guide

## Quick Deploy to Render (5 minutes)

### Prerequisites
- GitHub account (free)
- Render account (free)

---

## Step 1: Create GitHub Repository

### Option A: Using Git CLI
```bash
cd c:\Users\mano3\Desktop\Vera

# Initialize git
git init
git add .
git commit -m "Initial Vera bot commit"

# Create repo on GitHub and push
git remote add origin https://github.com/YOUR_USERNAME/vera-bot.git
git branch -M main
git push -u origin main
```

### Option B: Using GitHub Desktop
1. Go to https://github.com/new
2. Create new repo: `vera-bot`
3. Clone to `c:\Users\mano3\Desktop\Vera`
4. Drag files into folder
5. Commit & push

---

## Step 2: Deploy on Render

### 2.1 Sign Up
- Go to https://render.com
- Click "Get Started"
- Sign up with GitHub

### 2.2 Create Web Service
1. Click "New +" button
2. Select "Web Service"
3. Connect to your GitHub repo (`vera-bot`)
4. Click "Connect"

### 2.3 Configure Deployment
Fill in:
- **Name**: `vera-bot` (or any name)
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python bot.py`
- **Instance Type**: `Free` (sufficient for challenge)

### 2.4 Deploy
Click "Deploy" — Render builds and launches automatically (~2 min)

### 2.5 Get Your URL
Once deployed, you'll see:
```
Your site is live at: https://vera-bot-xxxxx.onrender.com
```

**Copy this URL** — you'll use it for submission.

---

## Step 3: Verify Bot is Live

Test your endpoints:

```powershell
$BOT_URL = "https://vera-bot-xxxxx.onrender.com"

# Test 1: Healthz
curl "$BOT_URL/v1/healthz"

# Test 2: Metadata  
curl "$BOT_URL/v1/metadata"

# Test 3: Push context
$body = @{
    scope = "merchant"
    context_id = "m_test"
    version = 1
    payload = @{ name = "Test" }
    delivered_at = [datetime]::UtcNow.ToString("O")
} | ConvertTo-Json

curl -X POST "$BOT_URL/v1/context" `
  -H "Content-Type: application/json" `
  -d $body

# Test 4: Tick
$tick = @{
    now = [datetime]::UtcNow.ToString("O")
    available_triggers = @()
} | ConvertTo-Json

curl -X POST "$BOT_URL/v1/tick" `
  -H "Content-Type: application/json" `
  -d $tick
```

All should return 200 OK ✓

---

## Step 4: Submit

Go to challenge submission form:
- **Submission URL**: `https://vera-bot-xxxxx.onrender.com`
- **Full name**: Your name
- **Email**: vera@magicpin.com (or yours)
- **Phone**: 9999999999 (or yours)
- **LinkedIn** (optional)

Click Submit ✓

---

## ⚠️ Important Notes

✓ **Keep bot running** — Don't delete the Render project during evaluation  
✓ **Free tier sleeps after 15 min of inactivity** — First request will be slow; that's OK  
✓ **No API key needed** — Bot uses fallback composition (functional but basic)  
✓ **All 5 endpoints required** — Judge will test all 5

---

## Troubleshooting

### Bot won't deploy
- Check `requirements.txt` syntax
- Ensure `bot.py` has no syntax errors
- Check logs in Render dashboard

### Endpoint returns 500
- Check Render logs for error messages
- Ensure dataset files are included in repo

### Slow first request
- Normal on free tier — Render spins down after inactivity
- 2nd+ requests will be instant

---

## Local Testing (Before Deploy)

```bash
# Test locally first
python bot.py

# In another terminal
curl http://localhost:8080/v1/healthz
```

Once working locally → Deploy to Render above.

---

**Status**: Once deployed, you're ready for Step 4 (submit form)! 🎉
