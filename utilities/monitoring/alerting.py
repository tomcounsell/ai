"""
Production alert management system.
Handles alert escalation, notification routing, and alert lifecycle.
"""

import logging
import smtplib
import json
import time
import threading
from typing import Dict, List, Optional, Callable, Any, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sqlite3
from enum import Enum
import os

class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning" 
    CRITICAL = "critical"
    EMERGENCY = "emergency"

class AlertStatus(Enum):
    """Alert lifecycle status"""
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"

@dataclass
class Alert:
    """Alert data structure"""
    id: str
    title: str
    message: str
    level: AlertLevel
    source: str
    timestamp: datetime
    status: AlertStatus = AlertStatus.OPEN
    metadata: Dict[str, Any] = field(default_factory=dict)
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    escalation_count: int = 0
    
@dataclass 
class AlertRule:
    """Alert rule configuration"""
    name: str
    condition: str
    level: AlertLevel
    cooldown_minutes: int = 15
    max_escalations: int = 3
    escalation_interval_minutes: int = 30
    enabled: bool = True
    tags: Set[str] = field(default_factory=set)

@dataclass
class NotificationChannel:
    """Notification delivery configuration"""
    name: str
    type: str  # 'email', 'slack', 'webhook', 'sms'
    config: Dict[str, Any]
    enabled: bool = True
    alert_levels: Set[AlertLevel] = field(default_factory=lambda: {AlertLevel.WARNING, AlertLevel.CRITICAL, AlertLevel.EMERGENCY})

class AlertManager:
    """
    Production alert management system.
    
    Features:
    - Multi-channel notifications (email, webhook, etc.)
    - Alert escalation and suppression
    - Alert correlation and deduplication
    - Historical alert tracking
    - Custom alert rules
    """
    
    def __init__(self, db_path: str = "data/monitoring.db"):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        
        # Alert state
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_rules: Dict[str, AlertRule] = {}
        self.notification_channels: Dict[str, NotificationChannel] = {}
        self.suppressed_alerts: Set[str] = set()
        
        # Escalation tracking
        self._escalation_timers: Dict[str, threading.Timer] = {}
        self._last_alert_times: Dict[str, datetime] = {}
        
        # Lock for thread safety
        self._lock = threading.RLock()
        
        # Initialize database and default configuration
        self._init_database()
        self._load_default_rules()
        self._load_default_channels()
    
    def _init_database(self):
        """Initialize alert database tables"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            # Alerts history table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alert_history (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    acknowledged_by TEXT,
                    acknowledged_at DATETIME,
                    resolved_at DATETIME,
                    escalation_count INTEGER DEFAULT 0,
                    metadata TEXT
                )
            ''')
            
            # Alert rules table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alert_rules (
                    name TEXT PRIMARY KEY,
                    condition TEXT NOT NULL,
                    level TEXT NOT NULL,
                    cooldown_minutes INTEGER DEFAULT 15,
                    max_escalations INTEGER DEFAULT 3,
                    escalation_interval_minutes INTEGER DEFAULT 30,
                    enabled BOOLEAN DEFAULT 1,
                    tags TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Notification channels table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS notification_channels (
                    name TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    config TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    alert_levels TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_timestamp ON alert_history(timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_level ON alert_history(level)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_status ON alert_history(status)')
    
    def _load_default_rules(self):
        """Load default alert rules"""
        default_rules = [
            AlertRule(
                name="high_cpu",
                condition="cpu_percent > 90",
                level=AlertLevel.CRITICAL,
                cooldown_minutes=10,
                tags={"system", "performance"}
            ),
            AlertRule(
                name="high_memory", 
                condition="memory_percent > 95",
                level=AlertLevel.CRITICAL,
                cooldown_minutes=10,
                tags={"system", "performance"}
            ),
            AlertRule(
                name="disk_full",
                condition="disk_percent > 95",
                level=AlertLevel.EMERGENCY,
                cooldown_minutes=5,
                tags={"system", "storage"}
            ),
            AlertRule(
                name="slow_database",
                condition="database_query_time > 5.0",
                level=AlertLevel.WARNING,
                cooldown_minutes=15,
                tags={"database", "performance"}
            ),
            AlertRule(
                name="system_down",
                condition="uptime_score < 50",
                level=AlertLevel.EMERGENCY,
                cooldown_minutes=1,
                tags={"system", "availability"}
            )
        ]
        
        for rule in default_rules:
            self.alert_rules[rule.name] = rule
    
    def _load_default_channels(self):
        """Load default notification channels"""
        # Email channel (requires configuration)
        if os.getenv('ALERT_EMAIL_SMTP_HOST'):
            email_config = {
                'smtp_host': os.getenv('ALERT_EMAIL_SMTP_HOST', 'localhost'),
                'smtp_port': int(os.getenv('ALERT_EMAIL_SMTP_PORT', '587')),
                'username': os.getenv('ALERT_EMAIL_USERNAME', ''),
                'password': os.getenv('ALERT_EMAIL_PASSWORD', ''),
                'from_email': os.getenv('ALERT_EMAIL_FROM', 'alerts@system.local'),
                'to_emails': os.getenv('ALERT_EMAIL_TO', '').split(','),
                'use_tls': os.getenv('ALERT_EMAIL_TLS', 'true').lower() == 'true'
            }
            
            self.notification_channels['email'] = NotificationChannel(
                name='email',
                type='email',
                config=email_config,
                alert_levels={AlertLevel.CRITICAL, AlertLevel.EMERGENCY}
            )
        
        # Webhook channel
        webhook_url = os.getenv('ALERT_WEBHOOK_URL')
        if webhook_url:
            webhook_config = {
                'url': webhook_url,
                'headers': json.loads(os.getenv('ALERT_WEBHOOK_HEADERS', '{}')),
                'timeout': int(os.getenv('ALERT_WEBHOOK_TIMEOUT', '30'))
            }
            
            self.notification_channels['webhook'] = NotificationChannel(
                name='webhook',
                type='webhook', 
                config=webhook_config,
                alert_levels={AlertLevel.WARNING, AlertLevel.CRITICAL, AlertLevel.EMERGENCY}
            )
    
    def add_rule(self, rule: AlertRule):
        """Add or update an alert rule"""
        with self._lock:
            self.alert_rules[rule.name] = rule
            self._save_rule_to_db(rule)
            self.logger.info(f"Alert rule '{rule.name}' added/updated")
    
    def remove_rule(self, rule_name: str):
        """Remove an alert rule"""
        with self._lock:
            if rule_name in self.alert_rules:
                del self.alert_rules[rule_name]
                self._delete_rule_from_db(rule_name)
                self.logger.info(f"Alert rule '{rule_name}' removed")
    
    def add_channel(self, channel: NotificationChannel):
        """Add or update a notification channel"""
        with self._lock:
            self.notification_channels[channel.name] = channel
            self._save_channel_to_db(channel)
            self.logger.info(f"Notification channel '{channel.name}' added/updated")
    
    def remove_channel(self, channel_name: str):
        """Remove a notification channel"""
        with self._lock:
            if channel_name in self.notification_channels:
                del self.notification_channels[channel_name]
                self._delete_channel_from_db(channel_name)
                self.logger.info(f"Notification channel '{channel_name}' removed")
    
    def create_alert(self, 
                    title: str,
                    message: str, 
                    level: AlertLevel,
                    source: str,
                    metadata: Optional[Dict[str, Any]] = None) -> Alert:
        """Create and process a new alert"""
        
        alert_id = f"{source}_{level.value}_{int(time.time())}"
        
        alert = Alert(
            id=alert_id,
            title=title,
            message=message,
            level=level,
            source=source,
            timestamp=datetime.now(),
            metadata=metadata or {}
        )
        
        return self._process_alert(alert)
    
    def _process_alert(self, alert: Alert) -> Alert:
        """Process alert through deduplication, suppression, and notification"""
        with self._lock:
            # Check for duplicate/similar alerts (deduplication)
            duplicate_id = self._find_duplicate_alert(alert)
            if duplicate_id:
                existing_alert = self.active_alerts[duplicate_id]
                existing_alert.escalation_count += 1
                existing_alert.timestamp = alert.timestamp
                self.logger.info(f"Alert deduplicated: {alert.title} (escalation #{existing_alert.escalation_count})")
                return existing_alert
            
            # Check suppression
            if self._is_suppressed(alert):
                self.logger.info(f"Alert suppressed: {alert.title}")
                return alert
            
            # Check cooldown period
            if self._in_cooldown(alert):
                self.logger.info(f"Alert in cooldown: {alert.title}")
                return alert
            
            # Add to active alerts
            self.active_alerts[alert.id] = alert
            self._last_alert_times[f"{alert.source}_{alert.level.value}"] = alert.timestamp
            
            # Save to database
            self._save_alert_to_db(alert)
            
            # Send notifications
            self._send_notifications(alert)
            
            # Schedule escalation if needed
            self._schedule_escalation(alert)
            
            self.logger.info(f"Alert created: {alert.title} [{alert.level.value}]")
            return alert
    
    def _find_duplicate_alert(self, alert: Alert) -> Optional[str]:
        """Find existing similar alert for deduplication"""
        for existing_id, existing_alert in self.active_alerts.items():
            if (existing_alert.source == alert.source and
                existing_alert.level == alert.level and
                existing_alert.title == alert.title and
                existing_alert.status == AlertStatus.OPEN):
                return existing_id
        return None
    
    def _is_suppressed(self, alert: Alert) -> bool:
        """Check if alert type is currently suppressed"""
        suppression_key = f"{alert.source}_{alert.level.value}"
        return suppression_key in self.suppressed_alerts
    
    def _in_cooldown(self, alert: Alert) -> bool:
        """Check if alert is in cooldown period"""
        cooldown_key = f"{alert.source}_{alert.level.value}"
        last_time = self._last_alert_times.get(cooldown_key)
        
        if not last_time:
            return False
        
        # Find matching rule for cooldown period
        cooldown_minutes = 15  # default
        for rule in self.alert_rules.values():
            if alert.source in rule.name or alert.level == rule.level:
                cooldown_minutes = rule.cooldown_minutes
                break
        
        cooldown_period = timedelta(minutes=cooldown_minutes)
        return (alert.timestamp - last_time) < cooldown_period
    
    def _send_notifications(self, alert: Alert):
        """Send alert through all applicable notification channels"""
        for channel_name, channel in self.notification_channels.items():
            if not channel.enabled:
                continue
                
            if alert.level not in channel.alert_levels:
                continue
                
            try:
                if channel.type == 'email':
                    self._send_email_notification(alert, channel)
                elif channel.type == 'webhook':
                    self._send_webhook_notification(alert, channel)
                else:
                    self.logger.warning(f"Unknown notification channel type: {channel.type}")
                    
            except Exception as e:
                self.logger.error(f"Failed to send notification via {channel_name}: {e}")
    
    def _send_email_notification(self, alert: Alert, channel: NotificationChannel):
        """Send email notification"""
        config = channel.config
        
        msg = MIMEMultipart()
        msg['From'] = config['from_email']
        msg['To'] = ', '.join(config['to_emails'])
        msg['Subject'] = f"[{alert.level.value.upper()}] {alert.title}"
        
        body = f"""
Alert Details:
--------------
Title: {alert.title}
Level: {alert.level.value.upper()}
Source: {alert.source}
Time: {alert.timestamp.isoformat()}

Message:
{alert.message}

Metadata:
{json.dumps(alert.metadata, indent=2)}

Alert ID: {alert.id}
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(config['smtp_host'], config['smtp_port'])
        if config.get('use_tls', True):
            server.starttls()
            
        if config.get('username') and config.get('password'):
            server.login(config['username'], config['password'])
            
        server.send_message(msg)
        server.quit()
        
        self.logger.info(f"Email notification sent for alert: {alert.id}")
    
    def _send_webhook_notification(self, alert: Alert, channel: NotificationChannel):
        """Send webhook notification"""
        import requests
        
        config = channel.config
        payload = {
            'alert_id': alert.id,
            'title': alert.title,
            'message': alert.message,
            'level': alert.level.value,
            'source': alert.source,
            'timestamp': alert.timestamp.isoformat(),
            'status': alert.status.value,
            'metadata': alert.metadata
        }
        
        headers = config.get('headers', {})
        headers['Content-Type'] = 'application/json'
        
        response = requests.post(
            config['url'],
            json=payload,
            headers=headers,
            timeout=config.get('timeout', 30)
        )
        
        response.raise_for_status()
        self.logger.info(f"Webhook notification sent for alert: {alert.id}")
    
    def _schedule_escalation(self, alert: Alert):
        """Schedule alert escalation"""
        # Find matching rule for escalation settings
        escalation_interval = 30  # default minutes
        max_escalations = 3
        
        for rule in self.alert_rules.values():
            if alert.source in rule.name or alert.level == rule.level:
                escalation_interval = rule.escalation_interval_minutes
                max_escalations = rule.max_escalations
                break
        
        if alert.escalation_count < max_escalations:
            timer = threading.Timer(
                escalation_interval * 60,
                self._escalate_alert,
                args=(alert.id,)
            )
            timer.start()
            self._escalation_timers[alert.id] = timer
    
    def _escalate_alert(self, alert_id: str):
        """Escalate an unresolved alert"""
        with self._lock:
            if alert_id not in self.active_alerts:
                return
                
            alert = self.active_alerts[alert_id]
            
            if alert.status != AlertStatus.OPEN:
                return
            
            alert.escalation_count += 1
            
            # Update alert level for escalation
            if alert.level == AlertLevel.WARNING:
                alert.level = AlertLevel.CRITICAL
            elif alert.level == AlertLevel.CRITICAL:
                alert.level = AlertLevel.EMERGENCY
            
            # Send escalated notification
            self._send_notifications(alert)
            
            # Schedule next escalation if needed
            self._schedule_escalation(alert)
            
            self.logger.warning(f"Alert escalated: {alert.title} (escalation #{alert.escalation_count})")
    
    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        """Acknowledge an alert"""
        with self._lock:
            if alert_id not in self.active_alerts:
                return False
            
            alert = self.active_alerts[alert_id]
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_by = acknowledged_by
            alert.acknowledged_at = datetime.now()
            
            # Cancel escalation timer
            if alert_id in self._escalation_timers:
                self._escalation_timers[alert_id].cancel()
                del self._escalation_timers[alert_id]
            
            self._update_alert_in_db(alert)
            self.logger.info(f"Alert acknowledged: {alert.title} by {acknowledged_by}")
            return True
    
    def resolve_alert(self, alert_id: str, resolved_by: str) -> bool:
        """Resolve an alert"""
        with self._lock:
            if alert_id not in self.active_alerts:
                return False
            
            alert = self.active_alerts[alert_id]
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = datetime.now()
            
            # Cancel escalation timer
            if alert_id in self._escalation_timers:
                self._escalation_timers[alert_id].cancel()
                del self._escalation_timers[alert_id]
            
            # Remove from active alerts
            del self.active_alerts[alert_id]
            
            self._update_alert_in_db(alert)
            self.logger.info(f"Alert resolved: {alert.title} by {resolved_by}")
            return True
    
    def suppress_alerts(self, source: str, level: AlertLevel, duration_minutes: int = 60):
        """Temporarily suppress alerts from a source/level"""
        suppression_key = f"{source}_{level.value}"
        self.suppressed_alerts.add(suppression_key)
        
        # Schedule automatic unsuppression
        timer = threading.Timer(
            duration_minutes * 60,
            self._unsuppress_alerts,
            args=(suppression_key,)
        )
        timer.start()
        
        self.logger.info(f"Alerts suppressed: {suppression_key} for {duration_minutes} minutes")
    
    def _unsuppress_alerts(self, suppression_key: str):
        """Remove alert suppression"""
        if suppression_key in self.suppressed_alerts:
            self.suppressed_alerts.remove(suppression_key)
            self.logger.info(f"Alert suppression removed: {suppression_key}")
    
    def get_active_alerts(self, level: Optional[AlertLevel] = None) -> List[Alert]:
        """Get list of active alerts"""
        with self._lock:
            alerts = list(self.active_alerts.values())
            if level:
                alerts = [a for a in alerts if a.level == level]
            return sorted(alerts, key=lambda x: x.timestamp, reverse=True)
    
    def get_alert_history(self, 
                         hours: int = 24,
                         level: Optional[AlertLevel] = None) -> List[Dict]:
        """Get alert history from database"""
        start_time = datetime.now() - timedelta(hours=hours)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if level:
                query = '''
                    SELECT * FROM alert_history 
                    WHERE timestamp >= ? AND level = ?
                    ORDER BY timestamp DESC
                '''
                params = (start_time, level.value)
            else:
                query = '''
                    SELECT * FROM alert_history 
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                '''
                params = (start_time,)
            
            results = conn.execute(query, params).fetchall()
            return [dict(row) for row in results]
    
    def _save_alert_to_db(self, alert: Alert):
        """Save alert to database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO alert_history (
                    id, title, message, level, source, status, timestamp,
                    acknowledged_by, acknowledged_at, resolved_at, escalation_count, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                alert.id, alert.title, alert.message, alert.level.value,
                alert.source, alert.status.value, alert.timestamp,
                alert.acknowledged_by, alert.acknowledged_at, alert.resolved_at,
                alert.escalation_count, json.dumps(alert.metadata)
            ))
    
    def _update_alert_in_db(self, alert: Alert):
        """Update existing alert in database"""
        self._save_alert_to_db(alert)
    
    def _save_rule_to_db(self, rule: AlertRule):
        """Save alert rule to database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO alert_rules (
                    name, condition, level, cooldown_minutes, max_escalations,
                    escalation_interval_minutes, enabled, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                rule.name, rule.condition, rule.level.value, rule.cooldown_minutes,
                rule.max_escalations, rule.escalation_interval_minutes, rule.enabled,
                json.dumps(list(rule.tags))
            ))
    
    def _delete_rule_from_db(self, rule_name: str):
        """Delete alert rule from database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM alert_rules WHERE name = ?', (rule_name,))
    
    def _save_channel_to_db(self, channel: NotificationChannel):
        """Save notification channel to database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO notification_channels (
                    name, type, config, enabled, alert_levels
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                channel.name, channel.type, json.dumps(channel.config),
                channel.enabled, json.dumps([level.value for level in channel.alert_levels])
            ))
    
    def _delete_channel_from_db(self, channel_name: str):
        """Delete notification channel from database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM notification_channels WHERE name = ?', (channel_name,))
    
    def cleanup(self):
        """Clean up resources and stop all timers"""
        with self._lock:
            # Cancel all escalation timers
            for timer in self._escalation_timers.values():
                timer.cancel()
            self._escalation_timers.clear()
            
            self.logger.info("Alert manager cleanup completed")