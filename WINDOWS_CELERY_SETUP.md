# 🚀 Celery OTP Cleanup Setup Guide
## Windows Local Development with Downloaded Redis (Option A)

---

## 📋 Prerequisites Checklist

### ✅ Required Software
- [ ] Python 3.11+ with virtual environment
- [ ] Git (for cloning/downloading Redis)
- [ ] PowerShell or Command Prompt

### ✅ Project Setup
- [ ] Django project in `d:\AoneGt\AoneGT\`
- [ ] Virtual environment activated
- [ ] Dependencies installed (`pip install -r requirements.txt`)

---

## 🖥️ Step 1: Install Redis (Option A - Direct Download)

### Download Redis for Windows
```bash
# Open a new terminal and navigate to a folder where you want to store Redis
cd C:\

# Download Redis (you can also download manually from GitHub)
# Go to: https://github.com/microsoftarchive/redis/releases
# Download: Redis-x64-3.2.100.zip (or latest version)

# Extract to C:\Redis (create the folder if needed)
# After extraction, you should have:
# C:\Redis\redis-server.exe
# C:\Redis\redis-cli.exe
```

### Verify Redis Installation
```bash
# Open Command Prompt or PowerShell
cd C:\Redis

# Test Redis server
redis-server.exe --version
# Should show: Redis server v=3.2.100 ...

# Test Redis CLI
redis-cli.exe --version
# Should show: redis-cli 3.2.100
```

---

## ⚙️ Step 2: Configure Environment Variables

### Create .env file (Already created)
Your `.env` file should contain:
```env
# Django Configuration
DJANGO_SECRET_KEY=your-secret-key-change-in-production-123456789
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database Configuration
DB_NAME=aonegt_db
DB_USER=postgres
DB_PASSWORD=4921
DB_HOST=localhost
DB_PORT=5432

# Email Configuration
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=yasminp32@gmail.com
EMAIL_HOST_PASSWORD=vaxwjwwfvoapbzxh
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=yasminp32@gmail.com
FRONTEND_RESET_URL=aonegt://reset-password

# Celery Configuration
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

---

## 🚀 Step 3: Daily Startup Commands

### Terminal 1: Start Redis Server
```bash
# Navigate to Redis installation directory
cd C:\Redis

# Start Redis server (keep this terminal open)
redis-server.exe
```

**Expected output:**
```
[XXXX] XX XXX XX:XX:XX.XXX # Server started, Redis version 3.2.100
[XXXX] XX XXX XX:XX:XX.XXX * The server is now ready to accept connections on port 6379
```

### Terminal 2: Start Celery Worker
```bash
# Navigate to project directory
cd d:\AoneGt\AoneGT

# Activate virtual environment
& d:\AoneGt\venv\Scripts\Activate.ps1

# Start Celery Worker (Windows-compatible mode)
celery -A aonegt worker --pool=solo -l info
```

**Expected output:**
```
celery@computer v5.3.4 (emerald-rush)

[config]
.> app:         aonegt:0xXXXXXXXXXXXXXXXX
.> transport:   redis://localhost:6379/0
.> results:     redis://localhost:6379/0
.> concurrency: 1 (solo)

[queues]
.> celery           exchange=celery(direct) key=celery

[tasks]
  . accounts.tasks.delete_expired_otps

[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] Connected to redis://localhost:6379/0
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] mingle: searching for neighbors
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] mingle: all alone
```

### Terminal 3: Start Celery Beat Scheduler
```bash
# Navigate to project directory
cd d:\AoneGt\AoneGT

# Activate virtual environment
& d:\AoneGt\venv\Scripts\Activate.ps1

# Start Celery Beat (scheduler)
celery -A aonegt beat -l info
```

**Expected output:**
```
celery beat v5.3.4 (emerald-rush) is starting.
__    -    ... __   -        _
LocalTime -> 2026-04-02 XX:XX:XX
Configuration ->
    . broker -> redis://localhost:6379/0
    . loader -> celery.loaders.app.AppLoader
    . scheduler -> celery.beat.PersistentScheduler
    . db -> celerybeat-schedule
    . logfile -> [stderr]@%INFO
    . maxinterval -> 5.00 minutes (300s)
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] beat: Starting...
```

---

## 📊 Step 4: Verify Everything is Working

### Check Redis Connection
```bash
# Open new terminal
cd C:\Redis

# Test connection
redis-cli.exe ping
# Should return: PONG
```

### Check Task Execution
Wait 2 minutes and look for logs in Celery Worker terminal:
```
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] Task accounts.tasks.delete_expired_otps[uuid] received
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] Successfully deleted X expired OTPs at 2026-04-02 XX:XX:XX
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] Task accounts.tasks.delete_expired_otps[uuid] succeeded in 0.XXXs
```

### Check Beat Scheduling
In Celery Beat terminal, you should see every 2 minutes:
```
[XXXX-XX-XX XX:XX:XX,XXX: INFO/MainProcess] Scheduler: Sending due task delete-expired-otps (accounts.tasks.delete_expired_otps)
```

---

## 🛠️ Troubleshooting Commands

### If Redis Won't Start
```bash
# Check if port 6379 is in use
netstat -ano | findstr :6379

# Kill conflicting process (replace XXXX with PID)
taskkill /PID XXXX /F

# Try different port
redis-server.exe --port 6380
# Then update .env: CELERY_BROKER_URL=redis://localhost:6380/0
```

### If Celery Commands Fail
```bash
# Check Python path
cd d:\AoneGt\AoneGT
python -c "import sys; print(sys.path)"

# Check Django settings
python manage.py check

# Test task manually
python manage.py shell
>>> from accounts.tasks import delete_expired_otps
>>> result = delete_expired_otps.delay()
>>> print(result.get())
```

### If Tasks Don't Execute
```bash
# Check Redis connectivity
redis-cli.exe
127.0.0.1:6379> KEYS *
127.0.0.1:6379> QUIT

# Check Celery worker status
celery -A aonegt inspect active
celery -A aonegt inspect scheduled
```

---

## 🛑 Shutdown Commands

### Stop All Services
```bash
# Close all three terminals (Ctrl+C in each)
# Or use task manager to end processes
```

### Quick Shutdown Script (Optional)
Create `stop-services.bat`:
```batch
@echo off
echo Stopping Celery services...
taskkill /IM celery.exe /F 2>nul
taskkill /IM redis-server.exe /F 2>nul
echo Services stopped.
```

---

## 📈 Monitoring Commands

### View Active Tasks
```bash
celery -A aonegt inspect active
```

### View Scheduled Tasks
```bash
celery -A aonegt inspect scheduled
```

### View Task Results
```bash
celery -A aonegt inspect results
```

### Monitor Redis
```bash
redis-cli.exe
127.0.0.1:6379> INFO
127.0.0.1:6379> QUIT
```

---

## 🔄 Daily Workflow Summary

### Morning Startup (3 Terminals):
```bash
# Terminal 1: Redis
cd C:\Redis && redis-server.exe

# Terminal 2: Worker
cd d:\AoneGt\AoneGT && & d:\AoneGt\venv\Scripts\Activate.ps1 && celery -A aonegt worker --pool=solo -l info

# Terminal 3: Beat
cd d:\AoneGt\AoneGT && & d:\AoneGt\venv\Scripts\Activate.ps1 && celery -A aonegt beat -l info
```

### Expected Behavior:
- **Every 2 minutes:** Beat sends task → Worker receives task → OTPs are deleted
- **Logs show:** "Successfully deleted X expired OTPs"
- **Database:** Expired unused OTPs disappear automatically

---

## ⚠️ Important Notes

### Windows-Specific Issues
- Use `--pool=solo` for Celery worker (Windows multiprocessing limitations)
- Keep all terminals open while developing
- Restart services if you see permission errors

### File Locations
- **Redis:** `C:\Redis\redis-server.exe`
- **Project:** `d:\AoneGt\AoneGT\`
- **Virtual Env:** `d:\AoneGt\venv\Scripts\Activate.ps1`
- **Environment:** `d:\AoneGt\AoneGT\.env`

### Port Usage
- **Redis:** Port 6379 (default)
- **Django:** Port 8000 (when running server)
- **PostgreSQL:** Port 5432 (if using database)

---

## 🎯 Quick Reference

| Service | Command | Status Check |
|---------|---------|--------------|
| **Redis** | `redis-server.exe` | `redis-cli ping` → PONG |
| **Worker** | `celery -A aonegt worker --pool=solo -l info` | Check for task logs |
| **Beat** | `celery -A aonegt beat -l info` | Check for "Sending due task" |

---

## 📞 Support

If issues persist:
1. Check all three terminals are running
2. Verify Redis connection with `redis-cli ping`
3. Check Celery worker shows "tasks: . accounts.tasks.delete_expired_otps"
4. Wait 2+ minutes for first task execution
5. Check database for deleted OTPs

**Created:** April 2, 2026
**Setup:** Windows + Downloaded Redis + Celery Solo Pool
**Purpose:** Automated OTP cleanup every 2 minutes</content>
<parameter name="filePath">d:\AoneGt\AoneGT\WINDOWS_CELERY_SETUP.md