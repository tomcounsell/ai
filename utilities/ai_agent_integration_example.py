"""Example integration of token tracking with the existing AI agent system.

This file shows practical examples of how to integrate token tracking into
the current PydanticAI-based agent system in this codebase.
"""

import asyncio
import uuid
from typing import Optional
from datetime import datetime

# Import existing components (these would be the actual imports in your codebase)
# from agents.valor.agent import valor_agent, ValorContext
# from tools.search_tool import search_web
# from integrations.telegram.handlers import handle_message

# Import token tracking
from utilities.token_decorators import (
    track_anthropic_tokens, TokenTrackingContext, BatchTokenTracker,
    track_manual_usage
)
from utilities.token_tracker import get_tracker


# Example 1: Tracking the main Valor agent interactions
async def run_valor_agent_with_tracking(
    message: str, 
    user_id: str = 'valor',
    context: Optional[dict] = None
):
    """
    Wrapper for the main Valor agent that adds manual token tracking.
    
    This shows how to manually track token usage when decorators aren't suitable.
    """
    # Simulate the existing agent call
    # In real implementation, this would be:
    # result = await valor_agent.run(message, deps=context or ValorContext())
    
    # Mock response for demo
    class MockUsage:
        def __init__(self, input_tokens, output_tokens):
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens
    
    class MockResult:
        def __init__(self, data, input_tokens, output_tokens):
            self.data = data
            self.usage = MockUsage(input_tokens, output_tokens)
    
    # Simulate processing
    input_tokens = len(message.split()) * 2
    output_tokens = max(50, input_tokens // 2)
    
    result = MockResult(
        data=f"Valor's response to: {message[:50]}...",
        input_tokens=input_tokens,
        output_tokens=output_tokens
    )
    
    # Manually track the usage
    track_manual_usage(
        project='ai_agent_system',
        host='Anthropic',
        model='claude-3-5-sonnet-20241022',
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        user_id=user_id,
        request_id=f"valor_{uuid.uuid4().hex[:8]}"
    )
    
    return result


# Example 2: Manual tracking for tools that don't have standard response formats
def search_web_with_tracking(
    query: str, 
    user_id: str = 'system',
    project: str = 'ai_agent_system'
) -> str:
    """
    Enhanced version of search_web tool with token tracking.
    
    This shows how to add tracking to existing tools that call external APIs.
    """
    # Call the existing search function
    # result = search_web(query)  # Your existing implementation
    
    # Mock for demo
    result = f"Search results for: {query}"
    
    # Manually track usage for the search API
    # Estimate tokens based on query and result length
    input_tokens = len(query.split()) * 1.5  # Rough estimate
    output_tokens = len(result.split()) * 1.3
    
    track_manual_usage(
        project=project,
        host='Perplexity',
        model='llama-3.1-sonar-small-128k-online',
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        user_id=user_id,
        request_id=f"search_{uuid.uuid4().hex[:8]}"
    )
    
    return result


# Example 3: Context-based tracking for complex operations
async def handle_telegram_message_with_tracking(
    message_text: str,
    chat_id: int,
    username: str
) -> str:
    """
    Telegram message handler with comprehensive token tracking.
    
    This shows how to track entire conversation flows, including
    multiple LLM calls within a single user interaction.
    """
    with TokenTrackingContext(
        project='telegram_bot',
        host='Anthropic',
        model='claude-3-5-sonnet-20241022',
        user_id=username,
        request_id=f"tg_{chat_id}_{uuid.uuid4().hex[:8]}"
    ) as tracker:
        
        # Initial message processing
        response_parts = []
        
        # 1. Process the main message
        main_response = await run_valor_agent_with_tracking(
            message_text, 
            user_id=username
        )
        response_parts.append(main_response.data)
        
        # Track the main interaction
        usage = main_response.usage
        tracker.add_usage(usage.input_tokens, usage.output_tokens)
        
        # 2. If message contains questions that need web search
        if any(word in message_text.lower() for word in ['latest', 'current', 'recent', 'news']):
            search_result = search_web_with_tracking(
                message_text, 
                user_id=username,
                project='telegram_bot'
            )
            
            # Process search results with the agent
            search_analysis = await run_valor_agent_with_tracking(
                f"Analyze this search result: {search_result}",
                user_id=username
            )
            response_parts.append(search_analysis.data)
            
            # Track the search analysis
            search_usage = search_analysis.usage
            tracker.add_usage(search_usage.input_tokens, search_usage.output_tokens)
        
        return "\n\n".join(response_parts)


# Example 4: Batch processing with token tracking
async def process_document_batch_with_tracking(
    documents: list[str],
    operation: str = 'summarize',
    user_id: str = 'batch_processor'
) -> list[str]:
    """
    Process multiple documents with batch token tracking.
    
    This shows how to track token usage across multiple related operations.
    """
    batch = BatchTokenTracker(
        project='document_processing',
        user_id=user_id,
        batch_id=f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    
    results = []
    
    for i, document in enumerate(documents):
        # Process each document
        prompt = f"Please {operation} this document: {document[:500]}..."
        
        result = await run_valor_agent_with_tracking(
            prompt,
            user_id=user_id
        )
        results.append(result.data)
        
        # Track usage for this document
        usage = result.usage
        batch.track_call(
            host='Anthropic',
            model='claude-3-5-sonnet-20241022',
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            request_id=f"doc_{i+1}"
        )
    
    # Log the entire batch
    total_calls = batch.log_batch()
    summary = batch.get_batch_summary()
    
    print(f"Processed {total_calls} documents")
    print(f"Total tokens: {summary['total_tokens']:,}")
    print(f"Average tokens per document: {summary['avg_tokens_per_call']:.1f}")
    
    return results


# Example 5: Usage monitoring and alerting
def check_usage_and_alert(
    project: str = 'ai_agent_system',
    alert_threshold: int = 100000  # tokens
) -> dict:
    """
    Check current usage and alert if thresholds are exceeded.
    
    This shows how to implement basic usage monitoring.
    """
    tracker = get_tracker()
    
    # Get usage for the last 24 hours
    from datetime import timedelta
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=1)
    
    daily_summary = tracker.get_usage_summary(
        project=project,
        start_date=start_date,
        end_date=end_date
    )
    
    total_tokens = daily_summary.get('total_tokens', 0)
    total_cost = daily_summary.get('total_cost_usd', 0)
    
    alert_status = {
        'project': project,
        'daily_tokens': total_tokens,
        'daily_cost': total_cost,
        'threshold_exceeded': total_tokens > alert_threshold,
        'alert_message': None
    }
    
    if alert_status['threshold_exceeded']:
        alert_status['alert_message'] = (
            f"⚠️ USAGE ALERT: {project} has used {total_tokens:,} tokens "
            f"in the last 24 hours (threshold: {alert_threshold:,}). "
            f"Cost: ${total_cost:.4f}"
        )
        print(alert_status['alert_message'])
    
    return alert_status


# Example 6: Usage reporting for different time periods
def generate_usage_report(
    project: str = 'ai_agent_system',
    days: int = 7
) -> dict:
    """
    Generate a comprehensive usage report for a project.
    """
    tracker = get_tracker()
    
    # Overall summary
    summary = tracker.get_usage_summary(project=project)
    
    # Daily breakdown
    daily_usage = tracker.get_daily_usage(days=days, project=project)
    
    # Model breakdown
    models = tracker.get_usage_by_model()
    project_models = [m for m in models if m.get('project') == project]
    
    report = {
        'project': project,
        'reporting_period_days': days,
        'overall_summary': summary,
        'daily_breakdown': daily_usage,
        'model_breakdown': project_models[:5],  # Top 5 models
        'recommendations': []
    }
    
    # Add cost optimization recommendations
    if summary.get('total_cost_usd', 0) > 10:
        report['recommendations'].append(
            "Consider using smaller models for simple tasks to reduce costs"
        )
    
    if summary.get('avg_tokens_per_request', 0) > 5000:
        report['recommendations'].append(
            "High average tokens per request - consider prompt optimization"
        )
    
    return report


# Demo function to show all examples
async def demo_ai_agent_integration():
    """Demonstrate all integration examples."""
    print("🤖 AI AGENT SYSTEM TOKEN TRACKING INTEGRATION DEMO")
    print("=" * 60)
    
    print("\n1. Basic agent interaction with tracking:")
    result = await run_valor_agent_with_tracking(
        "What's the weather like today?",
        user_id='demo_user'
    )
    print(f"   Response: {result.data}")
    print(f"   Tokens: {result.usage.input_tokens} + {result.usage.output_tokens}")
    
    print("\n2. Search tool with tracking:")
    search_result = search_web_with_tracking(
        "latest AI developments",
        user_id='demo_user'
    )
    print(f"   Search result: {search_result}")
    
    print("\n3. Telegram message handling:")
    telegram_response = await handle_telegram_message_with_tracking(
        "What are the latest trends in machine learning?",
        chat_id=12345,
        username='demo_user'
    )
    print(f"   Telegram response: {telegram_response[:100]}...")
    
    print("\n4. Batch document processing:")
    documents = [
        "Document 1: AI ethics and responsible development...",
        "Document 2: Machine learning model optimization...",
        "Document 3: Natural language processing advances..."
    ]
    batch_results = await process_document_batch_with_tracking(
        documents,
        operation='summarize',
        user_id='demo_user'
    )
    print(f"   Processed {len(batch_results)} documents")
    
    print("\n5. Usage monitoring:")
    alert_status = check_usage_and_alert()
    print(f"   Daily tokens: {alert_status['daily_tokens']:,}")
    print(f"   Daily cost: ${alert_status['daily_cost']:.4f}")
    
    print("\n6. Usage reporting:")
    report = generate_usage_report(days=1)
    print(f"   Total requests: {report['overall_summary'].get('request_count', 0)}")
    print(f"   Total tokens: {report['overall_summary'].get('total_tokens', 0):,}")
    
    print("\n✨ Integration demo completed!")
    print("💡 Next steps:")
    print("   1. Replace existing agent calls with tracked versions")
    print("   2. Add manual tracking to external API tools")
    print("   3. Implement usage monitoring in production")
    print("   4. Set up regular usage reports")


if __name__ == "__main__":
    # Run the demo
    asyncio.run(demo_ai_agent_integration())