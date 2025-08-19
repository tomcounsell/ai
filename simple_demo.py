#!/usr/bin/env python3
"""
Simple demo of the AI Rebuild system
Shows the system is working without requiring API keys
"""

import asyncio
from datetime import datetime, timezone
from utilities.database import DatabaseManager
from agents.valor.context import ValorContext, MessageEntry
from agents.context_manager import ContextWindowManager
from config import settings

async def main():
    print("\n" + "="*70)
    print("🤖 AI REBUILD SYSTEM - SIMPLE DEMO")
    print("="*70)
    
    # Initialize components
    print("\n📦 Initializing components...")
    
    # Database
    db = DatabaseManager()
    await db.initialize()
    print("✅ Database initialized")
    
    # Context Manager
    context_manager = ContextWindowManager(max_tokens=100000)
    print("✅ Context manager ready (100k token window)")
    
    # Create a test context
    context = ValorContext(
        chat_id="demo_session",
        user_name="Demo User",
        workspace="default"
    )
    print(f"✅ Context created for user: {context.user_name}")
    
    print("\n" + "-"*70)
    print("💬 INTERACTIVE DEMO")
    print("-"*70)
    print("Type messages to test the system (type 'quit' to exit)")
    print("Note: This is demo mode - no actual AI responses without API keys")
    print("-"*70 + "\n")
    
    message_count = 0
    
    while True:
        # Get user input
        user_input = input("You: ").strip()
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            break
        
        if not user_input:
            continue
        
        message_count += 1
        
        # Add to context
        message = MessageEntry(
            role="user",
            content=user_input,
            timestamp=datetime.now(timezone.utc)
        )
        context.message_history.append(message)
        
        # Get context stats
        stats = context_manager.get_context_stats(context)
        
        # Save to database
        try:
            project_id = 1  # Use default project
            # First ensure project exists
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO projects (project_id, name) VALUES (?, ?)",
                    (project_id, "Demo Project")
                )
            except:
                pass  # Project might already exist
            
            # Save message
            await db.execute(
                """INSERT INTO chat_history 
                   (project_id, session_id, role, content, timestamp) 
                   VALUES (?, ?, ?, ?, ?)""",
                (project_id, context.chat_id, "user", user_input, datetime.now(timezone.utc))
            )
            db_status = "✅ Saved to database"
        except Exception as e:
            db_status = f"⚠️  Database error: {e}"
        
        # Show response
        print(f"\n🤖 System: Message #{message_count} received!")
        print(f"   📊 Tokens used: {stats['token_usage']['total']}/{stats['token_usage']['max_tokens']}")
        print(f"   💾 {db_status}")
        print(f"   📝 Total messages in context: {stats['message_count']['total']}")
        
        # Check if compression needed
        if context_manager.needs_compression(context):
            print("   ⚠️  Context approaching limit - compression would be triggered")
        
        print()  # Empty line for readability
    
    # Cleanup
    await db.close()
    
    print("\n" + "="*70)
    print("✅ Demo completed successfully!")
    print(f"📊 Final stats: {message_count} messages processed")
    print("="*70 + "\n")

if __name__ == "__main__":
    print("Starting AI Rebuild System Demo...")
    print(f"Environment: {settings.environment}")
    print(f"Database: {settings.get_database_url()}")
    
    asyncio.run(main())