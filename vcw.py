import os
import logging
import sqlite3
import uuid
import random
import string
import time
import json
import requests
import asyncio
import sys
import subprocess
from datetime import datetime
from functools import wraps
from typing import Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler
)

# ---------------------------
# Configuration
# ---------------------------
BOT_TOKEN = os.getenv("CLOUDWAYS_BOT_TOKEN") or "8375060248:AAEOCPp8hU2lBYqDGt1SYwluQDQgqmDfWWA"
ADMIN_IDS = [6940098775]
REQUIRED_CHANNELS = ["@techyspyther"]

DB_PATH = "cloudways_bot.db"
DEFAULT_CREDITS = 10

CLOUDWAYS_SIGNUP_API = "https://api.cloudways.com/api/v2/guest/signup"

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("cloudways_bot")

# ---------------------------
# Threading decorator
# ---------------------------
def run_in_thread(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return wrapper

# ---------------------------
# Database Manager
# ---------------------------
class CloudwaysBot:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._initialize_database()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_database(self):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                credits INTEGER DEFAULT {DEFAULT_CREDITS},
                used INTEGER DEFAULT 0,
                last_request TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                password TEXT,
                first_name TEXT,
                last_name TEXT,
                status TEXT,
                risk_score INTEGER,
                verification_sent BOOLEAN,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                cloudways_response TEXT
            )
        """)
        conn.commit()
        conn.close()

    # ---------------------------
    # Restart Command
    # ---------------------------
    async def command_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized access.")
            return

        await update.message.reply_text("🔄 **Restarting bot and clearing data...**")

        try:
            logger.info(f"🔄 Restart initiated by: {user_id}")

            # Delete files
            files_deleted = self._delete_temp_files()

            # Reset database
            db_reset = self._reset_database()

            restart_message = (
                "🔄 **Bot Restarted Successfully** 🔄\n\n"
                f"🗑️ **Files Deleted:** `{files_deleted}`\n"
                f"🗄️ **Database Reset:** `{db_reset}`\n"
                f"⏰ **Timestamp:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n"
                "🔄 **Bot is restarting...**"
            )

            await update.message.reply_text(restart_message, parse_mode="Markdown")

            # Restart the bot
            await self._restart_bot()

        except Exception as e:
            error_message = f"❌ **Restart failed:** `{str(e)}`"
            await update.message.reply_text(error_message, parse_mode="Markdown")
            logger.error(f"Restart failed: {e}")

    def _delete_temp_files(self) -> int:
        """Delete temporary files"""
        files_deleted = 0
        try:
            files_to_delete = [
                "cloudways_bot.db",
                "cloudways_bot.log",
                "bot_session.txt",
                "user_data.json",
                "accounts_backup.json"
            ]

            for file_name in files_to_delete:
                if os.path.exists(file_name):
                    os.remove(file_name)
                    files_deleted += 1
                    logger.info(f"🗑️ Deleted file: {file_name}")

            # Delete all .db files
            for file in os.listdir("."):
                if file.endswith(".db") and os.path.isfile(file):
                    os.remove(file)
                    files_deleted += 1
                    logger.info(f"🗑️ Deleted database: {file}")

        except Exception as e:
            logger.error(f"Error deleting files: {e}")

        return files_deleted

    def _reset_database(self) -> bool:
        """Reset database tables"""
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            
            # Drop tables
            cur.execute("DROP TABLE IF EXISTS users")
            cur.execute("DROP TABLE IF EXISTS accounts")
            
            # Recreate tables
            self._initialize_database()
            
            conn.commit()
            conn.close()
            
            logger.info("🗄️ Database reset successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error resetting database: {e}")
            return False

    async def _restart_bot(self):
        """Restart the bot process"""
        try:
            logger.info("🔄 Restarting bot...")
            
            script_path = sys.argv[0]
            
            # Wait before restart
            await asyncio.sleep(2)
            
            # Restart the bot
            os.execv(sys.executable, ['python'] + sys.argv)
            
        except Exception as e:
            logger.error(f"❌ Error restarting bot: {e}")
            sys.exit(1)

    # ---------------------------
    # Channel subscription check
    # ---------------------------
    async def _check_channel_subscription(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        try:
            for channel in REQUIRED_CHANNELS:
                member = await context.bot.get_chat_member(channel, user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    return False
            return True
        except Exception:
            return False

    # ---------------------------
    # Broadcast Command
    # ---------------------------
    async def command_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized access.")
            return
            
        if not context.args:
            await update.message.reply_text("📝 Usage: /broadcast your message here")
            return
            
        message = " ".join(context.args)
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        users = cur.fetchall()
        conn.close()
        
        success = 0
        failed = 0
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=f"📢 **Broadcast** 📢\n\n{message}"
                )
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.1)
            
        await update.message.reply_text(f"📊 Broadcast results:\n✅ Success: {success}\n❌ Failed: {failed}")

    # ---------------------------
    # User Management
    # ---------------------------
    def add_or_update_user(self, user_id: int, username: str):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users (user_id, username, credits, used) VALUES (?, ?, ?, ?)",
                    (user_id, username, DEFAULT_CREDITS, 0))
        conn.commit()
        conn.close()

    def get_available_credits(self, user_id: int) -> int:
        if user_id in ADMIN_IDS:
            return 99999999
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT credits, used FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return 0
        return max(0, row["credits"] - row["used"])

    def use_credits(self, user_id: int, amount: int = 1) -> bool:
        if user_id in ADMIN_IDS:
            return True
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET used = used + ?, last_request = ?
            WHERE user_id = ? AND used + ? <= credits
        """, (amount, datetime.utcnow().isoformat(), user_id, amount))
        conn.commit()
        updated = cur.rowcount > 0
        conn.close()
        return updated

    def refund_credits(self, user_id: int, amount: int = 1):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET used = used - ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()

    # ---------------------------
    # Account Storage
    # ---------------------------
    def save_account(self, user_id: int, details: Dict[str, Any], result: Dict[str, Any], cloudways_response: str = ""):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO accounts (email, password, first_name, last_name, status, risk_score, verification_sent, user_id, cloudways_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            details.get("email"),
            details.get("password"),
            details.get("first_name"),
            details.get("last_name"),
            result.get("status", ""),
            int(result.get("risk_score", 0) or 0),
            1 if result.get("verification_sent") else 0,
            user_id,
            cloudways_response
        ))
        conn.commit()
        conn.close()

    # ---------------------------
    # Account Generation
    # ---------------------------
    def generate_account_details(self, email: str):
        try:
            r = requests.get("https://randomuser.me/api/?nat=us", timeout=8)
            r.raise_for_status()
            data = r.json()["results"][0]["name"]
            first = data["first"].capitalize()
            last = data["last"].capitalize()
        except Exception:
            first = random.choice(["John", "Vishalpapa", "Rajpapa", "Mike", "Alex", "David", "Sarah", "Emma"])
            last = random.choice(["Smith", "Brown", "Jones", "Patel", "Kumar"])
        
        password_base = random.choice(["txq", "flash"])
        password = f"{password_base}@{random.randint(1000,9999)}"
        
        return {"first_name": first, "last_name": last, "email": email, "password": password}

    # ---------------------------
    # Device fingerprint generation
    # ---------------------------
    def generate_device_fingerprint(self):
        device_id = str(uuid.uuid4())
        
        fingerprint = {
            "device_id": ''.join(random.choices(string.ascii_lowercase + string.digits, k=16)),
            "session_id": str(uuid.uuid4()),
            "os": random.choice(["Android 14","iOS 17","Windows 11","macOS 14"]),
            "os_build": f"{random.randint(10000,99999)}.{random.randint(10,99)}",
            "cpu": random.choice(["Snapdragon 8 Gen 2","Apple A17","Intel i7-12700K","M2 Pro"]),
            "gpu": random.choice(["Adreno 740","Apple GPU","NVIDIA RTX 4080"]),
            "ram": f"{random.choice([4,6,8,12,16,32])}GB",
            "storage": f"{random.choice([64,128,256,512])}GB",
            "lang": random.choice(["en-US","en-GB","hi-IN"]),
            "timezone": "+05:30",
            "battery_level": random.randint(20, 95),
            "charging": random.choice([True, False]),
            "screen": f"{random.choice([1080,1440,1920])}x{random.choice([1920,2160])}",
            "device_model": random.choice(["Pixel 7 Pro","iPhone 14 Pro","Samsung S23 Ultra","OnePlus 11"]),
            "network": random.choice(["WiFi","4G","5G"]),
            "browser": random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ]),
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "build_number": ''.join(random.choices(string.ascii_letters + string.digits, k=10)),
            "app_version": f"{random.randint(1,5)}.{random.randint(0,9)}.{random.randint(0,9)}",
            "device_country": random.choice(["US","IN","GB","CA","AU"]),
            "device_language": random.choice(["en","hi","es","fr"]),
            "device_timezone": random.choice(["Asia/Kolkata","America/New_York","Europe/London"]),
            "screen_density": random.choice([2.0, 3.0, 2.5, 3.5]),
            "font_scale": random.choice([1.0, 1.1, 0.9, 1.2]),
        }
        return device_id, fingerprint

    # ---------------------------
    # Cloudways API call (Sync)
    # ---------------------------
    def _call_cloudways_api_sync(self, details: Dict[str, str]) -> Dict[str, Any]:
        """
        Make a synchronous API call to Cloudways
        """
        try:
            device_id, fingerprint = self.generate_device_fingerprint()
            payload = {
                "first_name": details["first_name"],
                "last_name": details["last_name"],
                "email": details["email"],
                "password": details["password"],
                "gdpr_consent": True,
                "promo_code": "",
                "persona_tag_id": 13,
                "signup_price_id": "b",
                "talonData": fingerprint,
                "user_unique_id": str(uuid.uuid4()),
                "signup_page_template_id": 0
            }
            headers = {
                "User-Agent": fingerprint["browser"],
                "Content-Type": "application/json",
                "x-device-id": device_id
            }
            resp = requests.post(CLOUDWAYS_SIGNUP_API, json=payload, headers=headers, timeout=20)
            try:
                response_data = resp.json()
                return {"success": True, "data": response_data, "status_code": resp.status_code}
            except ValueError:
                return {"success": False, "error": f"Non-JSON response: {resp.status_code}", "raw": resp.text, "status_code": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e), "status_code": 0}

    @run_in_thread
    def call_cloudways_api(self, details):
        return self._call_cloudways_api_sync(details)

    # ---------------------------
    # Response parsing - ENHANCED VERSION
    # ---------------------------
    def parse_cloudways_response(self, resp: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if not resp.get("success"):
                return {
                    "success": False, 
                    "status": resp.get("error", "request_failed"),
                    "risk_score": 0,
                    "verification_sent": False,
                    "cloudways_response": resp,
                    "raw_response": resp
                }
            
            cloudways_data = resp.get("data", {})
            status_code = resp.get("status_code", 200)
            
            # Extract meaningful status from Cloudways response
            status_message = "unknown"
            if cloudways_data.get("message"):
                status_message = cloudways_data.get("message")
            elif cloudways_data.get("error"):
                status_message = cloudways_data.get("error")
            elif "data" in cloudways_data and cloudways_data["data"].get("message"):
                status_message = cloudways_data["data"].get("message")
            
            if status_code != 200:
                return {
                    "success": False,
                    "status": f"http_error_{status_code}: {status_message}",
                    "risk_score": 0,
                    "verification_sent": False,
                    "cloudways_response": cloudways_data,
                    "raw_response": resp
                }
            
            if "data" in cloudways_data and isinstance(cloudways_data["data"], dict):
                user_data = cloudways_data["data"].get("user", {})
                risk_score = user_data.get("risk_score", 0) or cloudways_data.get("risk_score", 0) or 0
                message = cloudways_data.get("message", "") or ""
                
                # Check if risk score is too high (100 or above)
                if risk_score >= 100:
                    return {
                        "success": False,
                        "status": f"High Risk Score ({risk_score}) - {status_message}",
                        "risk_score": risk_score,
                        "verification_sent": False,
                        "cloudways_response": cloudways_data,
                        "raw_response": resp
                    }
                
                return {
                    "success": True,
                    "status": status_message,
                    "risk_score": risk_score,
                    "verification_sent": "verify" in str(message).lower() or cloudways_data.get("verification_sent", False),
                    "cloudways_response": cloudways_data,
                    "raw_response": resp
                }
            
            if cloudways_data.get("success") is False:
                return {
                    "success": False, 
                    "status": cloudways_data.get("error") or cloudways_data.get("message") or "failed",
                    "risk_score": 0,
                    "verification_sent": False,
                    "cloudways_response": cloudways_data,
                    "raw_response": resp
                }
            
            return {
                "success": True, 
                "status": status_message, 
                "risk_score": cloudways_data.get("risk_score", 0),
                "verification_sent": False,
                "cloudways_response": cloudways_data,
                "raw_response": resp
            }
            
        except Exception as e:
            return {
                "success": False, 
                "status": f"parse_error:{e}",
                "risk_score": 0,
                "verification_sent": False,
                "cloudways_response": resp,
                "raw_response": resp
            }

    # ---------------------------
    # Format Cloudways response - ENHANCED VERSION
    # ---------------------------
    def format_cloudways_response(self, cloudways_response: Dict[str, Any]) -> str:
        """Format Cloudways API response for display"""
        try:
            if not cloudways_response:
                return "No response data"
            
            response_parts = []
            
            # Check main response fields
            if cloudways_response.get("error"):
                response_parts.append(f"❌ Error: {cloudways_response.get('error')}")
            
            if cloudways_response.get("message"):
                response_parts.append(f"📝 Message: {cloudways_response.get('message')}")
            
            if cloudways_response.get("status"):
                response_parts.append(f"📊 Status: {cloudways_response.get('status')}")
            
            # Check data section
            data_section = cloudways_response.get("data", {})
            if data_section:
                if data_section.get("message"):
                    response_parts.append(f"💬 Data Message: {data_section.get('message')}")
                
                if data_section.get("error"):
                    response_parts.append(f"⚠️ Data Error: {data_section.get('error')}")
                
                user_data = data_section.get("user", {})
                if user_data and isinstance(user_data, dict):
                    if user_data.get("risk_score") is not None:
                        response_parts.append(f"🎯 Risk Score: {user_data.get('risk_score')}")
                    if user_data.get("status"):
                        response_parts.append(f"👤 User Status: {user_data.get('status')}")
                    if user_data.get("email"):
                        response_parts.append(f"📧 User Email: {user_data.get('email')}")
            
            # If no specific messages found, show raw response for debugging
            if not response_parts:
                response_parts.append(" ")
                response_parts.append(str(cloudways_response)[:500] + "..." if len(str(cloudways_response)) > 500 else str(cloudways_response))
            
            return "\n".join(response_parts)
            
        except Exception as e:
            return f"Error parsing response: {str(e)}"

    # ---------------------------
    # Mass Account Creation
    # ---------------------------
    async def command_mass(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name or "User"
        
        if not await self._check_channel_subscription(user_id, context):
            await update.message.reply_text("❌ **You must join our Telegram channels to use this bot.**")
            return
            
        self.add_or_update_user(user_id, username)

        if not context.args:
            await update.message.reply_text("📝 **Usage:** `/mass email1.com email2.com email3.com ...`", parse_mode="Markdown")
            return

        emails = [email.strip() for email in context.args if "@" in email and "." in email.split("@")[-1]]
        
        if not emails:
            await update.message.reply_text("❌ **No valid email addresses found in your input.**")
            return

        available_credits = self.get_available_credits(user_id)
        if available_credits < len(emails):
            await update.message.reply_text(f"❌ **Insufficient credits.** You have `{available_credits}` credits but need `{len(emails)}`.", parse_mode="Markdown")
            return

        if not self.use_credits(user_id, len(emails)):
            await update.message.reply_text("💳 **No credits available. Please contact admin.**")
            return

        progress_msg = await update.message.reply_text(f"🔄 **Starting mass account creation for {len(emails)} emails...**")
        
        success_count = 0
        failed_count = 0
        results = []

        for i, email in enumerate(emails, 1):
            try:
                await progress_msg.edit_text(f"🔄 **Processing {i}/{len(emails)}: {email}**")
                
                details = self.generate_account_details(email)
                resp = await self.call_cloudways_api(details)
                result = self.parse_cloudways_response(resp)
                
                # Save account to database
                cloudways_response_json = json.dumps(resp.get("data", {}) if resp.get("success") else resp)
                self.save_account(user_id, details, result, cloudways_response_json)
                
                risk_score = result.get("risk_score", 0)

                if result.get("success") and risk_score < 100 and risk_score > 0:
                    success_count += 1
                    results.append(f"✅ **Success:** {email} | Risk: {risk_score}")
                else:
                    failed_count += 1
                    cloudways_text = self.format_cloudways_response(result.get("cloudways_response", {}))
                    if risk_score >= 100:
                        results.append(f"❌ **High Risk:** {email} | Risk: {risk_score} | {cloudways_text}")
                    else:
                        results.append(f"❌ **Failed:** {email} | {cloudways_text}")

                await asyncio.sleep(2)  # Rate limiting

            except Exception as e:
                failed_count += 1
                results.append(f"❌ **Error:** {email} | {str(e)}")
                continue

        # Generate report
        report = (
            "════════════════════════════════════\n"
            "        🎯 **Mass Creation Report** 🎯\n"
            "════════════════════════════════════\n\n"
            f"📧 **Total Emails:** `{len(emails)}`\n"
            f"✅ **Success:** `{success_count}`\n"
            f"❌ **Failed:** `{failed_count}`\n"
            f"💳 **Remaining Credits:** `{self.get_available_credits(user_id)}`\n\n"
            "────────────────────────────────\n"
            "📋 **Detailed Results:**\n"
        )
        
        # Limit results if too long
        results_text = "\n".join(results)
        if len(report + results_text) > 4000:
            results_text = "\n".join(results[:15]) + f"\n\n... and {len(results) - 15} more results"
        
        final_message = report + results_text + "\n\n════════════════════════════════════"
        
        await progress_msg.delete()
        await update.message.reply_text(final_message, parse_mode="Markdown")

        # Notify admins if successful
        if success_count > 0:
            admin_message = (
                "📢 **Mass Account Creation Completed** 📢\n\n"
                f"👤 **User:** {username} ({user_id})\n"
                f"📧 **Total:** {len(emails)} emails\n"
                f"✅ **Success:** {success_count}\n"
                f"❌ **Failed:** {failed_count}\n"
                f"💳 **Credits Used:** {len(emails)}"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(admin_id, admin_message, parse_mode="Markdown")
                except Exception:
                    pass

    # ---------------------------
    # Start Command
    # ---------------------------
    async def command_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name or "User"
        self.add_or_update_user(user_id, username)
        
        if not await self._check_channel_subscription(user_id, context):
            keyboard = [
                [InlineKeyboardButton("📢 Join Channel 1", url=f"https://t.me/{REQUIRED_CHANNELS[0][1:]}")],
                [InlineKeyboardButton("📢 Join Channel 2", url=f"https://t.me/{REQUIRED_CHANNELS[1][1:]}")],
                [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "════════════════════════════════\n"
                "✨ **Welcome to Cloudways Bot!** ✨\n"
                "════════════════════════════════\n\n"
                f"👤 **User:** @{username}\n"
                f"🆔 **ID:** `{user_id}`\n"
                f"💳 **Available Credits:** `{self.get_available_credits(user_id)}`\n\n"
                "📢 **To use this bot, you must join our Telegram channels.**\n"
                "────────────────────────────────\n"
                "👑 Owner = @S2_FLASH92 |\n"
                "────────────────────────────────",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return

        await update.message.reply_text(
            "👋 **Welcome to Cloudways Bot!** 👋\n\n"
            f"👤 **User:** @{username}\n"
            f"🆔 **ID:** `{user_id}`\n"
            f"💳 **Available Credits:** `{self.get_available_credits(user_id)}`\n\n"
            "📚 **Available Commands:**\n"
            "┌─────────────────────────────\n"
            "│ 💰 `/create email@example.com` \n"
            "│ 🔄 `/mass email1.com email2.com ...`\n"
            "│ 💳 `/credits` → Check your credits\n"
            "│ 📊 `/stats` → Bot statistics (admin)\n"
            "│ 🐛 `/debug email@example.com` → Debug mode (admin)\n"
            "│ 🔄 `/restart` → Restart bot & clear data (admin)\n"
            "└─────────────────────────────\n\n"
            "────────────────────────────────\n"
            "👑 Owner = @S2_FLASH92 |\n"
            "────────────────────────────────",
            parse_mode="Markdown"
        )

    async def callback_check_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "check_join":
            user_id = query.from_user.id
            if await self._check_channel_subscription(user_id, context):
                await query.edit_message_text(
                    "✅ **Successfully verified! You can now use the bot.**\n\n"
                    f"💳 **Available Credits:** `{self.get_available_credits(user_id)}`\n\n"
                    "📚 **Commands:**\n"
                    "💰 `/create email@example.com`\n"
                    "🔄 `/mass email1.com email2.com ...`\n"
                    "💳 `/credits` to check your balance",
                    parse_mode="Markdown"
                )
            else:
                keyboard = [
                    [InlineKeyboardButton("📢 Join Channel 1", url=f"https://t.me/{REQUIRED_CHANNELS[0][1:]}")],
                    [InlineKeyboardButton("📢 Join Channel 2", url=f"https://t.me/{REQUIRED_CHANNELS[1][1:]}")],
                    [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
                ]
                await query.edit_message_text(
                    "❌ **You haven't joined all required channels yet!**\n\n"
                    "Please join all channels listed above and try again.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

    async def command_credits(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        await update.message.reply_text(f"💳 **Available Credits:** `{self.get_available_credits(user_id)}`", parse_mode="Markdown")

    # ---------------------------
    # DEBUG COMMAND
    # ---------------------------
    async def command_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Debug command to test Cloudways API directly"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized access.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /debug email@example.com")
            return

        email = context.args[0]
        details = self.generate_account_details(email)
        
        await update.message.reply_text(f"🐛 **Debug mode for account creation:** `{email}`")
        
        resp = await self.call_cloudways_api(details)
        result = self.parse_cloudways_response(resp)
        
        debug_text = (
            f"📧 **Email:** {email}\n"
            f"✅ **Success:** {result.get('success')}\n"
            f"📊 **Status:** {result.get('status')}\n"
            f"🎯 **Risk Score:** {result.get('risk_score')}\n"
            f"📨 **Verification Sent:** {result.get('verification_sent')}\n\n"
            "📋 **Full Cloudways Response:**\n"
            f"```json\n{json.dumps(resp, indent=2)}\n```"
        )
        
        await update.message.reply_text(debug_text, parse_mode="Markdown")

    async def command_create(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name or "User"
        
        if not await self._check_channel_subscription(user_id, context):
            await update.message.reply_text("❌ **You must join our Telegram channels to use this bot.**")
            return
            
        self.add_or_update_user(user_id, username)

        if not context.args:
            await update.message.reply_text("📝 **Usage:** `/create email@example.com`", parse_mode="Markdown")
            return

        email = context.args[0].strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            await update.message.reply_text("❌ **Invalid email format.**")
            return

        if not self.use_credits(user_id):
            await update.message.reply_text("💳 **No credits available. Please contact admin.**")
            return

        details = self.generate_account_details(email)

        progress_msg = await update.message.reply_text("🔄 **Creating account...**")
        await asyncio.sleep(1)
        await progress_msg.edit_text("🔄 **Generating account details...**")
        await asyncio.sleep(2)
        await progress_msg.edit_text("🔄 **Sending request to Cloudways...**")
        await asyncio.sleep(1)

        try:
            await progress_msg.edit_text("🔄 **Making API call to Cloudways...**")
            resp = await self.call_cloudways_api(details)
            result = self.parse_cloudways_response(resp)
            
            # Save account to database
            cloudways_response_json = json.dumps(resp.get("data", {}) if resp.get("success") else resp)
            self.save_account(user_id, details, result, cloudways_response_json)
            
            risk_score = result.get("risk_score", 0)
            cloudways_response_text = self.format_cloudways_response(result.get("cloudways_response", {}))

            # Check if risk score is too high
            if risk_score >= 100:
                txt = (
                    "❌ **Account creation failed!** ❌\n\n"
                    f"📧 **Email:** `{details['email']}`\n"
                    f"⚠️ **Reason:** `High Risk Score - Account creation blocked`\n"
                    f"🎯 **Risk Score:** `{risk_score}`\n\n"
                    f"📋 **Cloudways Response:**\n`{cloudways_response_text}`\n\n"
                    f"💳 **Remaining Credits:** `{self.get_available_credits(user_id)}`"
                )
                await progress_msg.delete()
                await update.message.reply_text(txt, parse_mode="Markdown")
                # Refund credits for high risk
                self.refund_credits(user_id)
                return

            if risk_score == 0 or not result.get("success"):
                # Get detailed Cloudways response
                cloudways_response_text = self.format_cloudways_response(result.get("cloudways_response", {}))
                
                # Also check raw response for more details
                raw_response = result.get("raw_response", {})
                raw_response_text = self.format_cloudways_response(raw_response)
                
                txt = (
                    "❌ **Account creation failed!** ❌\n\n"
                )
                
                # Add raw response if different from parsed response
                if raw_response_text != cloudways_response_text:
                    txt += f"\n📋 **Response:**\n```\n{raw_response_text}\n```\n"
                
                txt += f"\n💳 **Remaining Credits:** `{self.get_available_credits(user_id)}`"
                
                await progress_msg.delete()
                await update.message.reply_text(txt, parse_mode="Markdown")
                # Refund credits
                self.refund_credits(user_id)
            else:
                txt = (
                    "════════════════════════════════════\n"
                    "     ✨ **Account Created Successfully!** ✨\n"
                    "════════════════════════════════════\n\n"
                    f"👤 **Name:** `{details['first_name']} {details['last_name']}`\n"
                    f"📧 **Email:** `{details['email']}`\n"
                    f"🔑 **Password:** `{details['password']}`\n"
                    f"📊 **Status:** `{result.get('status')}`\n"
                    f"🎯 **Risk Score:** `{risk_score}`\n"
                    f"📨 **Verification Sent:** `{result.get('verification_sent')}`\n"
                    "────────────────────────────────────────────────\n"
                    f"💳 **Remaining Credits:** `{self.get_available_credits(user_id)}`\n"
                    "────────────────────────────────────────────────\n"
                    "✅ **Success:** Account created successfully! 🎉\n\n"
                    "⚠️ **Note:** Risk Score below 100 = Verification Not Sent\n"
                    "════════════════════════════════════\n"
                    "     📢  »»───► TxQ | FLASH ◄───«« ** 📢\n"
                    "════════════════════════════════════"
                )

                await progress_msg.delete()
                await update.message.reply_text(txt, parse_mode="Markdown")

                # Notify admins
                owner_message = (
                    "📢 **New Account Created** 📢\n\n"
                    f"👤 **User:** {username} ({user_id})\n"
                    f"📧 **Email:** `{details['email']}`\n"
                    f"🔑 **Password:** `{details['password']}`\n"
                    f"📊 **Status:** `{result.get('status')}`\n"
                    f"🎯 **Risk Score:** `{risk_score}`"
                )
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(admin_id, owner_message, parse_mode="Markdown")
                    except Exception:
                        pass

        except Exception as e:
            await progress_msg.delete()
            await update.message.reply_text(f"💥 **Error:** `{str(e)}`")
            # Refund credits on error
            self.refund_credits(user_id)

    async def command_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized access.")
            return

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as total_users FROM users")
        total_users = cur.fetchone()["total_users"]
        cur.execute("SELECT COUNT(*) as total_accounts FROM accounts")
        total_accounts = cur.fetchone()["total_accounts"]
        cur.execute("SELECT SUM(credits) as total_credits FROM users")
        total_credits = cur.fetchone()["total_credits"] or 0
        cur.execute("SELECT SUM(used) as total_used FROM users")
        total_used = cur.fetchone()["total_used"] or 0
        conn.close()

        await update.message.reply_text(
            f"📊 **Bot Statistics** 📊\n\n"
            f"👥 **Total Users:** `{total_users}`\n"
            f"📧 **Total Accounts:** `{total_accounts}`\n"
            f"💳 **Total Credits:** `{total_credits}`\n"
            f"🔄 **Total Used:** `{total_used}`\n"
            f"📈 **Available:** `{total_credits - total_used}`",
            parse_mode="Markdown"
        )

    async def command_addcredits(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ Unauthorized access.")
            return

        if len(context.args) < 2:
            await update.message.reply_text("📝 Usage: /addcredits <user_id> <amount>")
            return

        try:
            target_user = int(context.args[0])
            amount = int(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid user_id or amount.")
            return

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, target_user))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Added `{amount}` credits to user `{target_user}`.", parse_mode="Markdown")

    # ---------------------------
    # Main bot runner
    # ---------------------------
    def run_bot(self):
        app = Application.builder().token(BOT_TOKEN).build()

        app.add_handler(CommandHandler("start", self.command_start))
        app.add_handler(CommandHandler("credits", self.command_credits))
        app.add_handler(CommandHandler("create", self.command_create))
        app.add_handler(CommandHandler("mass", self.command_mass))
        app.add_handler(CommandHandler("stats", self.command_stats))
        app.add_handler(CommandHandler("addcredits", self.command_addcredits))
        app.add_handler(CommandHandler("broadcast", self.command_broadcast))
        app.add_handler(CommandHandler("debug", self.command_debug))
        app.add_handler(CommandHandler("restart", self.command_restart))
        app.add_handler(CallbackQueryHandler(self.callback_check_join))

        logger.info("🤖 Cloudways Bot is starting...")
        app.run_polling()

# ---------------------------
# Entry point
# ---------------------------
if __name__ == "__main__":
    bot = CloudwaysBot()
    bot.run_bot()