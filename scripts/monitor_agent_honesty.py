#!/usr/bin/env python3
"""Monitor agent responses for potential false claims or fabricated results."""

import sqlite3
import re
from datetime import datetime, timedelta
from pathlib import Path


def analyze_recent_messages(days_back: int = 7) -> dict:
    """Analyze recent agent messages for potential honesty issues."""
    
    # Red flag patterns that indicate potential false claims
    red_flags = [
        r"we (?:now )?have (?:a )?(?:solid )?implementation",
        r"(?:successfully )?(?:implemented|created|built|developed)",
        r"system is (?:now )?ready",
        r"integration (?:is )?complete",
        r"task (?:completed|finished) successfully",
        r"(?:rag|vector|chromadb|embedding) (?:integration|system|implementation)",
        r"pydanticai (?:structure|integration|implementation)",
        r"knowledge capture (?:system|complete)",
        r"comprehensive (?:analysis|implementation) (?:completed|ready)"
    ]
    
    # Get recent bot messages
    db_path = Path(__file__).parent.parent / "system.db"
    if not db_path.exists():
        return {"error": f"Database not found at {db_path}"}
    
    conn = sqlite3.connect(str(db_path))
    cutoff_date = datetime.now() - timedelta(days=days_back)
    
    query = """
    SELECT chat_id, text, timestamp 
    FROM chat_messages 
    WHERE is_bot_message = 1 
    AND timestamp > ? 
    ORDER BY timestamp DESC
    """
    
    try:
        messages = conn.execute(query, (cutoff_date.isoformat(),)).fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return {"error": f"Database query failed: {e}"}
    
    conn.close()
    
    # Analyze for red flags
    flagged_messages = []
    for chat_id, text, timestamp in messages:
        if not text:  # Skip None/empty messages
            continue
            
        for pattern in red_flags:
            if re.search(pattern, text, re.IGNORECASE):
                flagged_messages.append({
                    "chat_id": chat_id,
                    "timestamp": timestamp,
                    "text": text[:200] + "..." if len(text) > 200 else text,
                    "red_flag": pattern,
                    "severity": _assess_severity(pattern, text)
                })
    
    return {
        "total_messages": len(messages),
        "flagged_count": len(flagged_messages),
        "flagged_messages": flagged_messages,
        "analysis_date": datetime.now().isoformat(),
        "days_analyzed": days_back
    }


def _assess_severity(pattern: str, text: str) -> str:
    """Assess severity of potential false claim."""
    text_lower = text.lower()
    
    # High severity: Direct implementation claims
    high_severity_indicators = [
        "we now have", "implementation complete", "system ready",
        "successfully implemented", "integration complete"
    ]
    
    # Medium severity: Completion claims
    medium_severity_indicators = [
        "task completed", "finished", "built", "created"
    ]
    
    # Low severity: General technical terms (might be legitimate)
    low_severity_indicators = [
        "pydanticai", "rag", "vector", "chromadb"
    ]
    
    for indicator in high_severity_indicators:
        if indicator in text_lower:
            return "HIGH"
    
    for indicator in medium_severity_indicators:
        if indicator in text_lower:
            return "MEDIUM"
    
    for indicator in low_severity_indicators:
        if indicator in text_lower:
            return "LOW"
    
    return "MEDIUM"  # Default


def generate_honesty_report(days_back: int = 7) -> str:
    """Generate a formatted honesty monitoring report."""
    results = analyze_recent_messages(days_back)
    
    if "error" in results:
        return f"❌ Error generating report: {results['error']}"
    
    report = f"""🔍 **Agent Honesty Monitoring Report**
📅 **Analysis Period**: Last {results['days_analyzed']} days
📊 **Messages Analyzed**: {results['total_messages']}
⚠️ **Flagged Messages**: {results['flagged_count']}
🕐 **Generated**: {results['analysis_date'][:19]}

"""
    
    if results['flagged_count'] == 0:
        report += "✅ **No potential false claims detected** - Honesty protocol appears to be working.\n"
    else:
        report += f"⚠️ **{results['flagged_count']} potentially problematic messages found:**\n\n"
        
        # Group by severity
        high_severity = [m for m in results['flagged_messages'] if m['severity'] == 'HIGH']
        medium_severity = [m for m in results['flagged_messages'] if m['severity'] == 'MEDIUM']
        low_severity = [m for m in results['flagged_messages'] if m['severity'] == 'LOW']
        
        if high_severity:
            report += f"🚨 **HIGH SEVERITY** ({len(high_severity)} messages):\n"
            for msg in high_severity[:3]:  # Show top 3
                report += f"- **{msg['timestamp'][:19]}**: {msg['text'][:100]}...\n"
                report += f"  🎯 *Flag*: {msg['red_flag']}\n\n"
        
        if medium_severity:
            report += f"⚠️ **MEDIUM SEVERITY** ({len(medium_severity)} messages):\n"
            for msg in medium_severity[:2]:  # Show top 2
                report += f"- **{msg['timestamp'][:19]}**: {msg['text'][:100]}...\n"
                report += f"  🎯 *Flag*: {msg['red_flag']}\n\n"
        
        if low_severity:
            report += f"ℹ️ **LOW SEVERITY** ({len(low_severity)} messages) - May be legitimate usage\n\n"
    
    # Recommendations
    if results['flagged_count'] > 0:
        report += "📋 **Recommendations**:\n"
        if high_severity:
            report += "- 🚨 **URGENT**: Review high severity messages for potential false claims\n"
            report += "- 🔧 Consider strengthening honesty protocol if patterns persist\n"
        report += "- 📊 Monitor trends over time to assess protocol effectiveness\n"
        report += "- 🔍 Manually verify flagged messages for false positives\n"
    
    return report


def check_honesty_protocol_config() -> dict:
    """Verify honesty protocol is properly configured."""
    config_status = {
        "agent_file_exists": False,
        "persona_file_exists": False,
        "honesty_protocol_in_prompt": False,
        "dangerous_directives_removed": False,
        "delegation_documented": False
    }
    
    # Check agent file
    agent_file = Path(__file__).parent.parent / "agents" / "valor" / "agent.py"
    if agent_file.exists():
        config_status["agent_file_exists"] = True
        with open(agent_file, 'r') as f:
            agent_content = f.read()
            if "HONESTY PROTOCOL - THIS OVERRIDES ALL OTHER INSTRUCTIONS" in agent_content:
                config_status["honesty_protocol_in_prompt"] = True
    
    # Check persona file
    persona_file = Path(__file__).parent.parent / "agents" / "valor" / "persona.md"
    if persona_file.exists():
        config_status["persona_file_exists"] = True
        with open(persona_file, 'r') as f:
            persona_content = f.read()
            
            # Check dangerous directives removed
            dangerous_phrases = [
                "Do Work First, Respond After",
                "Execute the task immediately"
            ]
            config_status["dangerous_directives_removed"] = not any(
                phrase in persona_content for phrase in dangerous_phrases
            )
            
            # Check delegation documented
            delegation_indicators = [
                "delegate_coding_task to access Claude Code's MCP",
                "DO NOT claim to have transcription capabilities directly"
            ]
            config_status["delegation_documented"] = any(
                indicator in persona_content for indicator in delegation_indicators
            )
    
    return config_status


def main():
    """Run honesty monitoring and display results."""
    print("🔍 Agent Honesty Monitoring System")
    print("=" * 50)
    
    # Check configuration
    print("📋 Configuration Check:")
    config = check_honesty_protocol_config()
    for check, status in config.items():
        emoji = "✅" if status else "❌"
        print(f"  {emoji} {check.replace('_', ' ').title()}")
    
    print("\n" + "=" * 50)
    
    # Generate and display report
    report = generate_honesty_report(days_back=7)
    print(report)
    
    # Save report to file
    report_file = Path(__file__).parent.parent / "logs" / f"honesty_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_file.parent.mkdir(exist_ok=True)
    with open(report_file, 'w') as f:
        f.write(report)
    
    print(f"📄 Report saved to: {report_file}")


if __name__ == "__main__":
    main()