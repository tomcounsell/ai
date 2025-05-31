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
    
    print("Example 1: PydanticAI Integration")
    print("```python")
    print("from utilities.token_tracker import log_token_usage")
    print("from pydantic_ai import Agent")
    print("")
    print("@agent.tool")
    print("def my_tool(ctx: RunContext, query: str) -> str:")
    print("    result = agent.run(query)")
    print("    ")
    print("    # Log token usage")
    print("    log_token_usage(")
    print("        project='my_ai_project',")
    print("        host='Anthropic',")
    print("        model='claude-3-5-sonnet-20241022',")
    print("        input_tokens=result.usage().input_tokens,")
    print("        output_tokens=result.usage().output_tokens,")
    print("        user_id=ctx.user_id,")
    print("        request_id=str(uuid.uuid4())")
    print("    )")
    print("    return result.data")
    print("```")
    
    print("\nExample 2: OpenAI Integration")
    print("```python")
    print("import openai")
    print("from utilities.token_tracker import log_token_usage")
    print("")
    print("def call_openai(prompt: str, project: str, user_id: str):")
    print("    response = openai.chat.completions.create(")
    print("        model='gpt-4o',")
    print("        messages=[{'role': 'user', 'content': prompt}]")
    print("    )")
    print("    ")
    print("    # Log usage")
    print("    log_token_usage(")
    print("        project=project,")
    print("        host='OpenAI',")
    print("        model='gpt-4o',")
    print("        input_tokens=response.usage.prompt_tokens,")
    print("        output_tokens=response.usage.completion_tokens,")
    print("        user_id=user_id,")
    print("        request_id=response.id")
    print("    )")
    print("    ")
    print("    return response.choices[0].message.content")
    print("```")
    
    print("\nExample 3: Report Generation")
    print("```bash")
    print("# View overall summary")
    print("python -m utilities.token_reports summary")
    print("")
    print("# View last 30 days by project")
    print("python -m utilities.token_reports projects --days 30")
    print("")
    print("# Export usage data")
    print("python -m utilities.token_reports export --output usage.csv --days 90")
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