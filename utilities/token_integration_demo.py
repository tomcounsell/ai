"""Enhanced demonstration of token tracking integration patterns.

This script shows comprehensive examples of how to integrate token tracking
into various AI workflows using decorators, context managers, and batch tracking.
"""

import os
import tempfile
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

try:
    from .token_tracker import TokenTracker, get_tracker
    from .token_decorators import (
        track_tokens, track_anthropic_tokens, track_openai_tokens,
        TokenTrackingContext, BatchTokenTracker, track_manual_usage
    )
except ImportError:
    from token_tracker import TokenTracker, get_tracker
    from token_decorators import (
        track_tokens, track_anthropic_tokens, track_openai_tokens,
        TokenTrackingContext, BatchTokenTracker, track_manual_usage
    )


# Mock response classes for demonstration
@dataclass
class MockOpenAIUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class MockOpenAIResponse:
    usage: MockOpenAIUsage
    id: str
    content: str


@dataclass
class MockAnthropicUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class MockAnthropicResponse:
    usage: MockAnthropicUsage
    content: str


def demo_decorator_patterns():
    """Demonstrate various decorator integration patterns."""
    print("=== DECORATOR INTEGRATION PATTERNS ===\n")
    
    # Pattern 1: OpenAI API with decorator
    @track_openai_tokens('demo_project', model='gpt-4o', user_id='demo_user')
    def call_openai_api(prompt: str) -> MockOpenAIResponse:
        """Simulate OpenAI API call with automatic token tracking."""
        token_count = len(prompt.split())
        return MockOpenAIResponse(
            usage=MockOpenAIUsage(
                prompt_tokens=token_count,
                completion_tokens=token_count // 2
            ),
            id=f"req_{int(time.time())}",
            content=f"Response to: {prompt[:50]}..."
        )
    
    print("1. OpenAI API with decorator:")
    response = call_openai_api("What is the capital of France and why is it important?")
    print(f"   Response: {response.content}")
    print(f"   Tokens: {response.usage.prompt_tokens} + {response.usage.completion_tokens}")
    
    # Pattern 2: Anthropic API with decorator
    @track_anthropic_tokens('demo_project', model='claude-3-5-sonnet-20241022', user_id='demo_user')
    def call_anthropic_api(message: str) -> MockAnthropicResponse:
        """Simulate Anthropic API call with automatic token tracking."""
        input_tokens = len(message.split()) * 2  # Rough estimate
        output_tokens = input_tokens // 3
        return MockAnthropicResponse(
            usage=MockAnthropicUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens
            ),
            content=f"Claude's response to your message about {message[:30]}..."
        )
    
    print("\n2. Anthropic API with decorator:")
    response = call_anthropic_api("Explain quantum computing in simple terms")
    print(f"   Response: {response.content}")
    print(f"   Tokens: {response.usage.input_tokens} + {response.usage.output_tokens}")
    
    # Pattern 3: Custom API with manual usage extraction
    def extract_custom_usage(response):
        try:
            from .token_decorators import TokenUsage
        except ImportError:
            from token_decorators import TokenUsage
        return TokenUsage(
            input_tokens=int(response['metadata']['input_count']),
            output_tokens=int(response['metadata']['output_count'])
        )
    
    @track_tokens(
        'demo_project', 'CustomAI', 'custom-model-v1',
        user_id='demo_user',
        extract_usage=extract_custom_usage
    )
    def call_custom_api(query: str) -> dict:
        """Simulate custom AI service with non-standard response format."""
        return {
            'result': f"Custom AI response to: {query}",
            'metadata': {
                'input_count': len(query.split()) * 1.5,
                'output_count': len(query.split()),
                'model_version': 'custom-v1'
            }
        }
    
    print("\n3. Custom API with usage extractor:")
    response = call_custom_api("How does machine learning work?")
    print(f"   Response: {response['result']}")
    print(f"   Input tokens: {response['metadata']['input_count']}")
    print(f"   Output tokens: {response['metadata']['output_count']}")


def demo_context_manager_patterns():
    """Demonstrate context manager usage patterns."""
    print("\n=== CONTEXT MANAGER PATTERNS ===\n")
    
    # Pattern 1: Single conversation tracking
    print("1. Single conversation with multiple exchanges:")
    with TokenTrackingContext('chat_demo', 'OpenAI', 'gpt-4o', user_id='demo_user') as tracker:
        # Simulate conversation turns
        messages = [
            "Hello, how are you?",
            "Can you help me with Python?",
            "What about machine learning?"
        ]
        
        for i, message in enumerate(messages):
            # Simulate API call
            input_tokens = len(message.split()) * 2
            output_tokens = input_tokens // 2
            
            print(f"   Turn {i+1}: {message}")
            print(f"   Tokens: {input_tokens} in, {output_tokens} out")
            
            tracker.add_usage(input_tokens, output_tokens)
    
    print("   ✓ Total conversation logged to database")
    
    # Pattern 2: Processing with error handling
    print("\n2. Batch processing with error handling:")
    documents = [
        "First document to analyze",
        "Second document with more content to process",
        "Third document for sentiment analysis"
    ]
    
    with TokenTrackingContext('batch_processing', 'Anthropic', 'claude-3-haiku', 
                             user_id='demo_user') as tracker:
        for i, doc in enumerate(documents):
            try:
                # Simulate processing
                input_tokens = len(doc.split()) * 3
                output_tokens = 50  # Fixed analysis output
                
                print(f"   Processing doc {i+1}: {doc[:30]}...")
                print(f"   Analysis tokens: {input_tokens} + {output_tokens}")
                
                tracker.add_usage(input_tokens, output_tokens)
                
            except Exception as e:
                print(f"   Error processing doc {i+1}: {e}")
                # Context manager still logs successful usage
    
    print("   ✓ Batch processing logged to database")


def demo_batch_tracking_patterns():
    """Demonstrate batch tracking patterns."""
    print("\n=== BATCH TRACKING PATTERNS ===\n")
    
    # Pattern 1: Multi-provider batch processing
    print("1. Multi-provider batch processing:")
    batch = BatchTokenTracker('multi_provider_demo', user_id='demo_user')
    
    tasks = [
        ("OpenAI", "gpt-4o", "Summarize this article"),
        ("Anthropic", "claude-3-sonnet", "Analyze sentiment"),
        ("OpenAI", "gpt-4o-mini", "Extract keywords"),
        ("Anthropic", "claude-3-haiku", "Translate text"),
    ]
    
    for i, (provider, model, task) in enumerate(tasks):
        # Simulate different token usage patterns
        if provider == "OpenAI":
            input_tokens = 100 + i * 20
            output_tokens = 50 + i * 10
        else:  # Anthropic
            input_tokens = 120 + i * 25
            output_tokens = 80 + i * 15
        
        print(f"   Task {i+1}: {task} ({provider}/{model})")
        print(f"   Tokens: {input_tokens} + {output_tokens}")
        
        batch.track_call(
            host=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=f"batch_task_{i+1}"
        )
    
    # Log the entire batch
    logged_count = batch.log_batch()
    summary = batch.get_batch_summary()
    
    print(f"\n   ✓ Logged {logged_count} tasks to database")
    print(f"   Total tokens: {summary['total_tokens']:,}")
    print(f"   Average per task: {summary['avg_tokens_per_call']:.1f}")
    
    # Pattern 2: Data pipeline batch tracking
    print("\n2. Data pipeline batch tracking:")
    pipeline_batch = BatchTokenTracker('data_pipeline', user_id='pipeline_user')
    
    # Simulate data pipeline stages
    stages = [
        ("data_extraction", 500, 100),
        ("data_cleaning", 300, 50),
        ("feature_extraction", 800, 200),
        ("model_inference", 1200, 400),
        ("result_formatting", 200, 150)
    ]
    
    for stage, input_tokens, output_tokens in stages:
        print(f"   Stage: {stage}")
        print(f"   Tokens: {input_tokens} + {output_tokens}")
        
        pipeline_batch.track_call(
            host="Anthropic",
            model="claude-3-5-sonnet-20241022",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=f"pipeline_{stage}"
        )
    
    pipeline_batch.log_batch()
    pipeline_summary = pipeline_batch.get_batch_summary()
    
    print(f"\n   ✓ Pipeline complete: {pipeline_summary['total_tokens']:,} total tokens")


def demo_manual_tracking_patterns():
    """Demonstrate manual tracking patterns."""
    print("\n=== MANUAL TRACKING PATTERNS ===\n")
    
    print("1. Legacy system integration:")
    
    # Simulate legacy API calls that don't fit decorator patterns
    legacy_calls = [
        ("legacy_system_1", "proprietary_model", 150, 75),
        ("legacy_system_2", "old_nlp_model", 200, 100),
        ("custom_inference", "fine_tuned_model", 300, 180)
    ]
    
    for system, model, input_tokens, output_tokens in legacy_calls:
        record_id = track_manual_usage(
            project='legacy_integration',
            host=system,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user_id='legacy_user',
            request_id=f"legacy_{system}_{int(time.time())}"
        )
        
        print(f"   {system}/{model}: {input_tokens}+{output_tokens} tokens")
        print(f"   Record ID: {record_id}")
    
    print("\n2. Real-time tracking for streaming responses:")
    
    # Simulate streaming response tracking
    streaming_tokens = [(50, 0), (0, 25), (0, 30), (0, 45)]  # Input, then chunks
    
    with TokenTrackingContext('streaming_demo', 'OpenAI', 'gpt-4o',
                             user_id='stream_user') as tracker:
        for i, (input_chunk, output_chunk) in enumerate(streaming_tokens):
            if input_chunk > 0:
                print(f"   Sent prompt: {input_chunk} tokens")
            if output_chunk > 0:
                print(f"   Received chunk {i}: {output_chunk} tokens")
            
            tracker.add_usage(input_chunk, output_chunk)
            time.sleep(0.1)  # Simulate streaming delay
    
    print("   ✓ Streaming response fully tracked")


def demo_reporting_and_analysis():
    """Demonstrate reporting capabilities."""
    print("\n=== REPORTING AND ANALYSIS ===\n")
    
    tracker = get_tracker()
    
    # Overall summary
    print("1. Overall usage summary:")
    summary = tracker.get_usage_summary()
    print(f"   Total requests: {summary['request_count']:,}")
    print(f"   Total tokens: {summary['total_tokens']:,}")
    print(f"   Total cost: ${summary['total_cost_usd']:.4f}")
    print(f"   Avg tokens/request: {summary['avg_tokens_per_request']:.1f}")
    
    # By project
    print("\n2. Usage by project:")
    projects = tracker.get_usage_by_project()
    for project in projects[:5]:  # Top 5 projects
        print(f"   {project['project']}: {project['total_tokens']:,} tokens, "
              f"${project['total_cost_usd']:.4f}")
    
    # By model
    print("\n3. Usage by model:")
    models = tracker.get_usage_by_model()
    for model in models[:5]:  # Top 5 models
        print(f"   {model['host']}/{model['model']}: {model['total_tokens']:,} tokens")
    
    # Daily usage
    print("\n4. Recent daily usage:")
    daily = tracker.get_daily_usage(days=7)
    for day in daily[:3]:  # Last 3 days with data
        print(f"   {day['date']}: {day['total_tokens']:,} tokens, "
              f"${day['total_cost_usd']:.4f}")
    
    # Export sample
    print("\n5. Data export sample:")
    csv_data = tracker.export_usage_data(format="csv")
    lines = csv_data.split('\n')
    print(f"   Header: {lines[0]}")
    if len(lines) > 1:
        print(f"   Sample: {lines[1]}")
    print(f"   Total records available: {len(lines)-2}")


def run_comprehensive_demo():
    """Run the complete token tracking integration demo."""
    print("🚀 COMPREHENSIVE TOKEN TRACKING INTEGRATION DEMO")
    print("=" * 60)
    
    # Create temporary database for demo
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()
    
    try:
        # Initialize with temp database
        import utilities.token_tracker
        utilities.token_tracker._tracker = None  # Reset global tracker
        tracker = TokenTracker(temp_db.name)
        utilities.token_tracker._tracker = tracker
        
        print(f"📁 Demo database: {temp_db.name}")
        print()
        
        # Run all demo patterns
        demo_decorator_patterns()
        demo_context_manager_patterns()
        demo_batch_tracking_patterns()
        demo_manual_tracking_patterns()
        demo_reporting_and_analysis()
        
        print("\n" + "=" * 60)
        print("✨ DEMO COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        
        print(f"\n📊 Final Statistics:")
        final_summary = tracker.get_usage_summary()
        print(f"   Total demo requests: {final_summary['request_count']}")
        print(f"   Total demo tokens: {final_summary['total_tokens']:,}")
        print(f"   Estimated demo cost: ${final_summary['total_cost_usd']:.4f}")
        
        print(f"\n💾 Demo artifacts:")
        print(f"   Database file: {temp_db.name}")
        print(f"   Size: {os.path.getsize(temp_db.name)} bytes")
        print("\n   This database contains realistic demo data and can be")
        print("   inspected or used for testing reporting tools.")
        
        return temp_db.name
        
    except Exception as e:
        print(f"❌ Demo failed: {e}")
        if os.path.exists(temp_db.name):
            os.unlink(temp_db.name)
        raise


if __name__ == "__main__":
    db_path = run_comprehensive_demo()
    
    print("\n🔧 Next steps:")
    print("1. Integrate decorators into your AI workflows")
    print("2. Use context managers for complex operations")
    print("3. Implement batch tracking for bulk processing")
    print("4. Set up regular reporting and cost monitoring")
    print("5. Add alerts for usage thresholds")
    
    print(f"\n🗃️  To explore the demo database:")
    print(f"   python -m utilities.token_reports summary")
    print(f"   PYTHONPATH=. python utilities/token_reports.py projects")
    print(f"   # (Set PYTHONPATH or run from project root)")