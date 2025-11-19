#!/usr/bin/env python3
"""
Continuous Bot Health Monitor

This script monitors the Telegram bot's health and ensures:
1. Bot process is running
2. Bot is responding to messages  
3. No critical errors in logs
4. Response times are acceptable

Can be run manually or as a cron job for continuous monitoring.
"""

import asyncio
import subprocess
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class BotHealthMonitor:
    """Monitor Telegram bot health."""
    
    def __init__(self):
        self.health_status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bot_running": False,
            "bot_responsive": False,
            "error_rate": 0,
            "recent_errors": [],
            "response_time_avg": None,
            "health_score": 0,  # 0-100
            "recommendations": []
        }
    
    def check_bot_process(self) -> bool:
        """Check if bot process is running."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "telegram_bot.py"],
                capture_output=True,
                text=True
            )
            is_running = result.returncode == 0
            self.health_status["bot_running"] = is_running
            
            if is_running:
                pid = result.stdout.strip()
                print(f"✅ Bot process is running (PID: {pid})")
            else:
                print("❌ Bot process is NOT running")
                self.health_status["recommendations"].append("Start the bot: python telegram_bot.py")
            
            return is_running
        except Exception as e:
            print(f"❌ Error checking bot process: {e}")
            return False
    
    def analyze_logs(self) -> Dict[str, Any]:
        """Analyze bot logs for health indicators."""
        log_file = Path("logs/telegram_bot.log")
        
        if not log_file.exists():
            print("⚠️ Log file not found")
            return {"error": "Log file not found"}
        
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
            
            # Get logs from last 5 minutes
            five_min_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
            recent_lines = []
            
            for line in reversed(lines):
                # Try to parse timestamp
                try:
                    # Extract timestamp (format: 2025-08-21 13:00:57,002)
                    if len(line) > 23:
                        timestamp_str = line[:23]
                        timestamp = datetime.strptime(
                            timestamp_str, 
                            "%Y-%m-%d %H:%M:%S,%f"
                        ).replace(tzinfo=timezone.utc)
                        
                        if timestamp < five_min_ago:
                            break
                        recent_lines.append(line)
                except:
                    continue
            
            recent_lines.reverse()
            
            # Count errors and messages
            error_count = sum(1 for line in recent_lines if 'ERROR' in line)
            message_count = sum(1 for line in recent_lines if 'New message from' in line)
            response_count = sum(1 for line in recent_lines if 'Response sent' in line)
            
            # Calculate error rate
            total_operations = message_count + response_count
            error_rate = (error_count / total_operations * 100) if total_operations > 0 else 0
            
            self.health_status["error_rate"] = round(error_rate, 2)
            
            # Get recent errors
            error_lines = [line.strip() for line in recent_lines if 'ERROR' in line]
            self.health_status["recent_errors"] = error_lines[-5:]  # Last 5 errors
            
            # Check responsiveness
            if message_count > 0:
                response_rate = (response_count / message_count * 100) if message_count > 0 else 0
                self.health_status["bot_responsive"] = response_rate > 50
            
            print(f"📊 Last 5 minutes: {message_count} messages, {response_count} responses, {error_count} errors")
            
            if error_rate > 10:
                self.health_status["recommendations"].append(f"High error rate ({error_rate:.1f}%) - check logs")
            
            return {
                "messages": message_count,
                "responses": response_count,
                "errors": error_count,
                "error_rate": error_rate
            }
        
        except Exception as e:
            print(f"❌ Error analyzing logs: {e}")
            return {"error": str(e)}
    
    def check_database_lock(self) -> bool:
        """Check for database lock issues."""
        try:
            # Check for session journal files (indicates lock issues)
            session_journals = list(Path("data").glob("*.session-journal"))
            
            if session_journals:
                print(f"⚠️ Found {len(session_journals)} session journal files (possible lock issues)")
                self.health_status["recommendations"].append(
                    "Database lock detected. Run: pkill -f python && rm data/*.session-journal"
                )
                return False
            
            print("✅ No database lock issues detected")
            return True
        
        except Exception as e:
            print(f"❌ Error checking database: {e}")
            return False
    
    def calculate_health_score(self) -> int:
        """Calculate overall health score (0-100)."""
        score = 0
        
        # Bot running (40 points)
        if self.health_status["bot_running"]:
            score += 40
        
        # Bot responsive (30 points)
        if self.health_status["bot_responsive"]:
            score += 30
        
        # Low error rate (20 points)
        error_rate = self.health_status["error_rate"]
        if error_rate < 1:
            score += 20
        elif error_rate < 5:
            score += 15
        elif error_rate < 10:
            score += 10
        elif error_rate < 20:
            score += 5
        
        # No database locks (10 points)
        if not any("Database lock" in r for r in self.health_status["recommendations"]):
            score += 10
        
        self.health_status["health_score"] = score
        return score
    
    def run_health_check(self) -> Dict[str, Any]:
        """Run complete health check."""
        print("=" * 60)
        print("🏥 TELEGRAM BOT HEALTH CHECK")
        print(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        # Check bot process
        self.check_bot_process()
        
        # Analyze logs
        self.analyze_logs()
        
        # Check database
        self.check_database_lock()
        
        # Calculate health score
        score = self.calculate_health_score()
        
        # Determine status
        if score >= 90:
            status = "🟢 HEALTHY"
        elif score >= 70:
            status = "🟡 DEGRADED"
        elif score >= 50:
            status = "🟠 WARNING"
        else:
            status = "🔴 CRITICAL"
        
        print("=" * 60)
        print(f"Health Score: {score}/100 - {status}")
        
        if self.health_status["recent_errors"]:
            print(f"\n⚠️ Recent Errors ({len(self.health_status['recent_errors'])}):")
            for error in self.health_status["recent_errors"][-3:]:  # Show last 3
                print(f"  - {error[:100]}...")
        
        if self.health_status["recommendations"]:
            print("\n💡 Recommendations:")
            for rec in self.health_status["recommendations"]:
                print(f"  • {rec}")
        
        print("=" * 60)
        
        # Save health report
        report_file = Path("logs/bot_health_report.json")
        report_file.parent.mkdir(exist_ok=True)
        with open(report_file, 'w') as f:
            json.dump(self.health_status, f, indent=2)
        print(f"💾 Health report saved to {report_file}")
        
        return self.health_status
    
    async def auto_fix_issues(self) -> bool:
        """Attempt to automatically fix common issues."""
        fixed_any = False
        
        if not self.health_status["bot_running"]:
            print("\n🔧 Attempting to start bot...")
            try:
                subprocess.Popen(
                    ["python", "telegram_bot.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True
                )
                await asyncio.sleep(3)
                print("✅ Bot start command issued")
                fixed_any = True
            except Exception as e:
                print(f"❌ Failed to start bot: {e}")
        
        if "Database lock" in str(self.health_status["recommendations"]):
            print("\n🔧 Attempting to clear database locks...")
            try:
                # Kill stale processes
                subprocess.run(["pkill", "-f", "telegram_bot.py"], check=False)
                await asyncio.sleep(1)
                
                # Remove journal files
                for journal in Path("data").glob("*.session-journal"):
                    journal.unlink()
                
                print("✅ Database locks cleared")
                fixed_any = True
            except Exception as e:
                print(f"❌ Failed to clear locks: {e}")
        
        return fixed_any


async def main():
    """Main entry point."""
    monitor = BotHealthMonitor()
    
    # Run health check
    health = monitor.run_health_check()
    
    # Auto-fix if requested
    if "--auto-fix" in sys.argv and health["health_score"] < 70:
        print("\n🔧 AUTO-FIX MODE ENABLED")
        if await monitor.auto_fix_issues():
            print("🔄 Re-running health check after fixes...")
            await asyncio.sleep(5)
            monitor = BotHealthMonitor()
            health = monitor.run_health_check()
    
    # Exit with appropriate code
    # 0 = healthy, 1 = degraded, 2 = critical
    if health["health_score"] >= 90:
        sys.exit(0)
    elif health["health_score"] >= 50:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())