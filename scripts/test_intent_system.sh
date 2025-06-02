#!/bin/bash

# Intent System Test Management Script
#
# This script provides easy commands to test different aspects
# of the intent classification and prompt generation system.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo -e "${BLUE}🧠 Intent System Test Management${NC}"
echo "=================================="

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if Ollama is running
check_ollama() {
    print_status "Checking Ollama availability..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        print_status "✅ Ollama is running"
        return 0
    else
        print_warning "⚠️  Ollama not available - tests will use fallback"
        return 1
    fi
}

# Function to run quick tests
run_quick_tests() {
    print_status "Running quick intent tests..."
    cd "$PROJECT_DIR"
    python tests/run_intent_tests.py --quick
}

# Function to run specific intent tests
run_intent_tests() {
    local intent="$1"
    print_status "Testing intent: $intent"
    cd "$PROJECT_DIR"
    python tests/run_intent_tests.py --intent "$intent"
}

# Function to run prompt generation tests
run_prompt_tests() {
    print_status "Testing prompt generation..."
    cd "$PROJECT_DIR"
    python tests/run_intent_tests.py --prompts
}

# Function to run integration tests
run_integration_tests() {
    print_status "Testing integration pipeline..."
    cd "$PROJECT_DIR"
    python tests/run_intent_tests.py --integration
}

# Function to run comprehensive tests
run_comprehensive_tests() {
    print_status "Running comprehensive test suite..."
    cd "$PROJECT_DIR"
    python tests/test_intent_prompt_combinations.py
}

# Function to run pytest tests
run_pytest() {
    print_status "Running pytest test suite..."
    cd "$PROJECT_DIR"
    if command -v pytest >/dev/null 2>&1; then
        pytest tests/test_intent_prompt_combinations.py -v
    else
        print_warning "pytest not available, running with python"
        python -m pytest tests/test_intent_prompt_combinations.py -v
    fi
}

# Function to test specific message
test_message() {
    local message="$1"
    print_status "Testing message: '$message'"
    cd "$PROJECT_DIR"
    python -c "
import asyncio
from integrations.ollama_intent import classify_message_intent

async def test():
    result = await classify_message_intent('$message', {})
    print(f'Intent: {result.intent.value}')
    print(f'Confidence: {result.confidence:.2f}')
    print(f'Reasoning: {result.reasoning}')
    print(f'Emoji: {result.suggested_emoji}')

asyncio.run(test())
"
}

# Function to benchmark performance
run_performance_tests() {
    print_status "Running performance benchmarks..."
    cd "$PROJECT_DIR"
    python tests/test_intent_prompt_combinations.py --performance
}

# Function to show test results
show_test_results() {
    print_status "Recent test results:"
    if [ -f "$PROJECT_DIR/test_report.txt" ]; then
        cat "$PROJECT_DIR/test_report.txt"
    else
        print_warning "No test report found. Run tests first."
    fi
}

# Function to clean test artifacts
clean_tests() {
    print_status "Cleaning test artifacts..."
    cd "$PROJECT_DIR"
    rm -f test_report.txt
    rm -f __pycache__/test_*.pyc
    rm -rf tests/__pycache__
    print_status "✅ Test artifacts cleaned"
}

# Function to validate test environment
validate_environment() {
    print_status "Validating test environment..."
    
    cd "$PROJECT_DIR"
    
    # Check Python imports
    python -c "
import sys
try:
    from integrations.ollama_intent import classify_message_intent
    print('✅ Intent classification module available')
except ImportError as e:
    print(f'❌ Intent module error: {e}')
    sys.exit(1)

try:
    from integrations.intent_prompts import get_intent_system_prompt
    print('✅ Intent prompts module available')
except ImportError as e:
    print(f'❌ Prompts module error: {e}')
    sys.exit(1)

try:
    from agents.valor.handlers import handle_telegram_message_with_intent
    print('✅ Agent handlers module available')
except ImportError as e:
    print(f'❌ Agent module error: {e}')
    sys.exit(1)

print('✅ All required modules available')
"
    
    # Check Ollama
    check_ollama || true  # Don't fail if Ollama unavailable
    
    print_status "✅ Environment validation complete"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  quick                    Run quick intent tests"
    echo "  intent <INTENT_NAME>     Test specific intent"
    echo "  prompts                  Test prompt generation"
    echo "  integration             Test integration pipeline"
    echo "  comprehensive           Run comprehensive test suite"
    echo "  pytest                  Run pytest test suite"
    echo "  message \"<MESSAGE>\"      Test classification of specific message"
    echo "  performance             Run performance benchmarks"
    echo "  results                 Show recent test results"
    echo "  validate                Validate test environment"
    echo "  clean                   Clean test artifacts"
    echo "  help                    Show this help"
    echo ""
    echo "Available intents:"
    echo "  casual_chat, question_answer, project_query, development_task,"
    echo "  image_generation, image_analysis, web_search, link_analysis,"
    echo "  system_health, unclear"
    echo ""
    echo "Examples:"
    echo "  $0 quick"
    echo "  $0 intent casual_chat"
    echo "  $0 message \"What's my project status?\""
    echo "  $0 comprehensive"
}

# Main command processing
case "${1:-help}" in
    "quick")
        validate_environment
        run_quick_tests
        ;;
    "intent")
        if [ -z "$2" ]; then
            print_error "Intent name required"
            show_usage
            exit 1
        fi
        validate_environment
        run_intent_tests "$2"
        ;;
    "prompts")
        validate_environment
        run_prompt_tests
        ;;
    "integration")
        validate_environment
        run_integration_tests
        ;;
    "comprehensive")
        validate_environment
        run_comprehensive_tests
        ;;
    "pytest")
        validate_environment
        run_pytest
        ;;
    "message")
        if [ -z "$2" ]; then
            print_error "Message text required"
            show_usage
            exit 1
        fi
        validate_environment
        test_message "$2"
        ;;
    "performance")
        validate_environment
        run_performance_tests
        ;;
    "results")
        show_test_results
        ;;
    "validate")
        validate_environment
        ;;
    "clean")
        clean_tests
        ;;
    "help"|*)
        show_usage
        ;;
esac