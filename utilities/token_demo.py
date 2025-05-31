"""Demonstration script for token usage tracking system.

This script shows how to use the token tracking system with realistic examples.
"""

import os
import tempfile
from datetime import datetime, timedelta
from utilities.token_tracker import TokenTracker, log_token_usage


def demo_basic_usage():
    """Demonstrate basic token tracking functionality."""
    print("=== Token Tracking System Demo ===\n")
    
    # Create a temporary database for demo
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()
    
    try:
        # Initialize tracker
        tracker = TokenTracker(temp_db.name)
        print(f"✓ Initialized database: {temp_db.name}")
        
        # Log some sample usage
        print("\n📝 Logging token usage...")
        
        # Simulate Claude conversation
        tracker.log_usage(
            project="ai_agent_system",
            host="Anthropic",
            model="claude-3-5-sonnet-20241022",
            input_tokens=1200,
            output_tokens=800,
            user_id="valor",
            request_id="conv_001"
        )
        
        # Simulate OpenAI API call
        tracker.log_usage(
            project="ai_agent_system",
            host="OpenAI",
            model="gpt-4o",
            input_tokens=950,
            output_tokens=600,
            user_id="valor",
            request_id="api_002"
        )
        
        # Simulate another project
        tracker.log_usage(
            project="notion_integration",
            host="Anthropic",
            model="claude-3-5-haiku-20241022",
            input_tokens=450,
            output_tokens=200,
            user_id="valor",
            request_id="notion_003"
        )
        
        # Add some usage from yesterday
        yesterday = datetime.utcnow() - timedelta(days=1)
        tracker.log_usage(
            project="ai_agent_system",
            host="OpenAI",
            model="gpt-4o-mini",
            input_tokens=2000,
            output_tokens=1500,
            user_id="valor",
            timestamp=yesterday,
            request_id="batch_004"
        )
        
        print("✓ Logged 4 usage records")
        
        # Show overall summary
        print("\n📊 Overall Usage Summary:")
        summary = tracker.get_usage_summary()
        print(f"   Total Requests: {summary['request_count']}")
        print(f"   Total Tokens: {summary['total_tokens']:,}")
        print(f"   Total Cost: ${summary['total_cost_usd']:.4f}")
        print(f"   Avg Tokens/Request: {summary['avg_tokens_per_request']:.1f}")
        
        # Show usage by project
        print("\n📈 Usage by Project:")
        projects = tracker.get_usage_by_project()
        for project in projects:
            print(f"   {project['project']}: {project['total_tokens']:,} tokens, ${project['total_cost_usd']:.4f}")
        
        # Show usage by model
        print("\n🤖 Usage by Model:")
        models = tracker.get_usage_by_model()
        for model in models[:3]:  # Show top 3
            print(f"   {model['host']}/{model['model']}: {model['total_tokens']:,} tokens")
        
        # Show daily usage
        print("\n📅 Daily Usage (last 7 days):")
        daily = tracker.get_daily_usage(days=7)
        for day in daily[:3]:  # Show last 3 days with data
            print(f"   {day['date']}: {day['total_tokens']:,} tokens, ${day['total_cost_usd']:.4f}")
        
        # Show project-specific summary
        print("\n🎯 AI Agent System Project Summary:")
        ai_summary = tracker.get_usage_summary(project="ai_agent_system")
        print(f"   Requests: {ai_summary['request_count']}")
        print(f"   Tokens: {ai_summary['total_tokens']:,}")
        print(f"   Cost: ${ai_summary['total_cost_usd']:.4f}")
        
        # Export data
        print("\n💾 Exporting usage data...")
        csv_data = tracker.export_usage_data(format="csv")
        export_file = temp_db.name.replace(".db", "_export.csv")
        with open(export_file, 'w') as f:
            f.write(csv_data)
        print(f"✓ Exported to: {export_file}")
        
        print(f"\n🗑️  Demo database files:")
        print(f"   Database: {temp_db.name}")
        print(f"   Export: {export_file}")
        print("   (These files will remain for inspection)")
        
    except Exception as e:
        print(f"❌ Error during demo: {e}")
        if os.path.exists(temp_db.name):
            os.unlink(temp_db.name)


def demo_convenience_functions():
    """Demonstrate convenience functions."""
    print("\n=== Convenience Functions Demo ===\n")
    
    # Use the convenience function
    print("📝 Using log_token_usage convenience function...")
    
    record_id = log_token_usage(
        project="demo_project",
        host="Anthropic", 
        model="claude-3-5-sonnet-20241022",
        input_tokens=500,
        output_tokens=300,
        user_id="demo_user"
    )
    
    print(f"✓ Logged usage with record ID: {record_id}")
    
    # Get tracker instance
    from utilities.token_tracker import get_tracker
    tracker = get_tracker()
    
    summary = tracker.get_usage_summary(project="demo_project")
    print(f"✓ Demo project has {summary['total_tokens']} tokens logged")


def demo_integration_examples():
    """Show integration examples for common AI frameworks."""
    print("\n=== Integration Examples ===\n")
    
    print("Example 1: PydanticAI Integration (Current Codebase)")
    print("```python")
    print("# agents/valor/agent.py - Integrate with existing agent")
    print("from utilities.token_decorators import track_anthropic_tokens")
    print("from pydantic_ai import Agent, RunContext")
    print("")
    print("# Wrap the run_valor_agent function")
    print("@track_anthropic_tokens('ai_agent_system', user_id='valor')")
    print("async def run_valor_agent_tracked(message: str, context: ValorContext = None):")
    print("    if context is None:")
    print("        context = ValorContext()")
    print("    result = await valor_agent.run(message, deps=context)")
    print("    return result  # PydanticAI result with usage() method")
    print("```")
    
    print("\nExample 2: Tool Integration with Manual Tracking")
    print("```python")
    print("# tools/search_tool.py - Add tracking to web search")
    print("from utilities.token_tracker import log_token_usage")
    print("import uuid")
    print("")
    print("def search_web_tracked(query: str, user_id: str = None) -> str:")
    print("    # Call existing search function")
    print("    result = search_web(query)")
    print("    ")
    print("    # Log usage for Perplexity API call")
    print("    log_token_usage(")
    print("        project='ai_agent_system',")
    print("        host='Perplexity',")
    print("        model='llama-3.1-sonar-small-128k-online',")
    print("        input_tokens=len(query.split()) * 1.3,  # Estimate")
    print("        output_tokens=len(result.split()) * 1.3,  # Estimate")
    print("        user_id=user_id,")
    print("        request_id=str(uuid.uuid4())")
    print("    )")
    print("    return result")
    print("```")
    
    print("\nExample 3: Telegram Integration with Context Tracking")
    print("```python")
    print("# integrations/telegram/handlers.py - Track per chat/user")
    print("from utilities.token_decorators import TokenTrackingContext")
    print("")
    print("async def handle_ai_message(message, chat_id, username):")
    print("    with TokenTrackingContext(")
    print("        project='telegram_bot',")
    print("        host='Anthropic',")
    print("        model='claude-3-5-sonnet-20241022',")
    print("        user_id=username")
    print("    ) as tracker:")
    print("        response = await run_valor_agent(message.text)")
    print("        ")
    print("        # Track tokens used in conversation")
    print("        tracker.set_usage(")
    print("            input_tokens=len(message.text.split()) * 1.5,")
    print("            output_tokens=len(response.split()) * 1.5")
    print("        )")
    print("    return response")
    print("```")
    
    print("\nExample 4: Report Generation")
    print("```bash")
    print("# View overall summary")
    print("python -m utilities.token_reports summary")
    print("")
    print("# View last 30 days by project")
    print("python -m utilities.token_reports projects --days 30")
    print("")
    print("# View usage by user")
    print("python -m utilities.token_reports summary --days 7 | grep valor")
    print("")
    print("# Export usage data")
    print("python -m utilities.token_reports export --output usage.csv --days 90")
    print("```")
    
    print("\nExample 5: Batch Processing with Multiple LLM Calls")
    print("```python")
    print("# For processing multiple items in tools")
    print("from utilities.token_decorators import BatchTokenTracker")
    print("")
    print("def process_multiple_images(image_paths: list, user_id: str):")
    print("    batch = BatchTokenTracker('image_processing', user_id=user_id)")
    print("    ")
    print("    results = []")
    print("    for image_path in image_paths:")
    print("        result = analyze_image(image_path)  # Your existing function")
    print("        results.append(result)")
    print("        ")
    print("        # Track each call")
    print("        batch.track_call('OpenAI', 'gpt-4o-vision', 100, 75)")
    print("    ")
    print("    # Log all at once")
    print("    batch.log_batch()")
    print("    return results")
    print("```")


if __name__ == "__main__":
    demo_basic_usage()
    demo_convenience_functions()
    demo_integration_examples()
    
    print("\n✨ Demo completed successfully!")
    print("\nNext steps:")
    print("1. Integrate token tracking into your AI workflows")
    print("2. Use reports to monitor usage and costs")
    print("3. Set up regular exports for accounting/analysis")
    print("4. Consider adding alerts for usage thresholds")