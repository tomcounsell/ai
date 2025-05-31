#!/usr/bin/env python3
"""
Message Lifecycle Test Runner

Runs comprehensive tests for the message lifecycle through Telegram and Valor handlers.
"""

import os
import sys
import unittest
import asyncio
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_lifecycle_tests():
    """Run all message lifecycle tests."""
    print("🧪 Running Message Lifecycle Tests")
    print("=" * 60)
    
    # Discover and run tests
    loader = unittest.TestLoader()
    
    # Load comprehensive lifecycle tests
    print("\n📋 Loading comprehensive lifecycle tests...")
    try:
        from tests.test_message_lifecycle_comprehensive import (
            TestMessageLifecycleStage1,
            TestMessageLifecycleStage2, 
            TestMessageLifecycleStage3,
            TestMessageLifecycleStage4,
            TestMessageLifecycleStage5,
            TestMessageLifecycleStage6,
            TestMessageLifecycleStage7,
            TestMessageLifecycleIntegration
        )
        
        lifecycle_suite = unittest.TestSuite()
        for test_class in [TestMessageLifecycleStage1, TestMessageLifecycleStage2,
                          TestMessageLifecycleStage3, TestMessageLifecycleStage4,
                          TestMessageLifecycleStage5, TestMessageLifecycleStage6,
                          TestMessageLifecycleStage7, TestMessageLifecycleIntegration]:
            lifecycle_suite.addTests(loader.loadTestsFromTestCase(test_class))
        
        print(f"   ✅ Loaded {lifecycle_suite.countTestCases()} comprehensive tests")
    except ImportError as e:
        print(f"   ❌ Failed to load comprehensive tests: {e}")
        lifecycle_suite = unittest.TestSuite()
    
    # Load integration point tests
    print("\n🔗 Loading integration point tests...")
    try:
        from tests.test_handler_integration_points import (
            TestChatHistoryIntegration,
            TestIntentIntegration,
            TestSecurityIntegration,
            TestErrorHandlingIntegration,
            TestPerformanceIntegration
        )
        
        integration_suite = unittest.TestSuite()
        for test_class in [TestChatHistoryIntegration, TestIntentIntegration,
                          TestSecurityIntegration, TestErrorHandlingIntegration,
                          TestPerformanceIntegration]:
            integration_suite.addTests(loader.loadTestsFromTestCase(test_class))
        
        print(f"   ✅ Loaded {integration_suite.countTestCases()} integration tests")
    except ImportError as e:
        print(f"   ❌ Failed to load integration tests: {e}")
        integration_suite = unittest.TestSuite()
    
    # Combine all test suites
    all_tests = unittest.TestSuite([lifecycle_suite, integration_suite])
    total_tests = all_tests.countTestCases()
    
    if total_tests == 0:
        print("\n❌ No tests found to run!")
        return False
    
    print(f"\n🚀 Running {total_tests} total tests...")
    print("-" * 60)
    
    # Run tests with detailed output
    runner = unittest.TextTestRunner(
        verbosity=2,
        stream=sys.stdout,
        descriptions=True,
        failfast=False
    )
    
    result = runner.run(all_tests)
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 Test Results Summary")
    print("=" * 60)
    
    if result.wasSuccessful():
        print(f"✅ All {result.testsRun} tests passed!")
        print("\n🎉 Message lifecycle testing complete - all systems operational!")
        return True
    else:
        print(f"❌ {len(result.failures)} failures, {len(result.errors)} errors out of {result.testsRun} tests")
        
        if result.failures:
            print(f"\n💥 Failures ({len(result.failures)}):")
            for test, traceback in result.failures:
                print(f"   - {test}: {traceback.split('AssertionError:')[-1].strip() if 'AssertionError:' in traceback else 'See details above'}")
        
        if result.errors:
            print(f"\n🔥 Errors ({len(result.errors)}):")
            for test, traceback in result.errors:
                error_msg = traceback.split('\n')[-2] if traceback.split('\n') else "Unknown error"
                print(f"   - {test}: {error_msg}")
        
        return False


def run_specific_stage(stage_number):
    """Run tests for a specific lifecycle stage."""
    stage_classes = {
        1: "TestMessageLifecycleStage1",
        2: "TestMessageLifecycleStage2", 
        3: "TestMessageLifecycleStage3",
        4: "TestMessageLifecycleStage4",
        5: "TestMessageLifecycleStage5",
        6: "TestMessageLifecycleStage6",
        7: "TestMessageLifecycleStage7"
    }
    
    if stage_number not in stage_classes:
        print(f"❌ Invalid stage number: {stage_number}. Must be 1-7.")
        return False
    
    print(f"🧪 Running Stage {stage_number} Tests")
    print("=" * 40)
    
    try:
        # Import and run specific stage
        module = __import__('tests.test_message_lifecycle_comprehensive', fromlist=[stage_classes[stage_number]])
        test_class = getattr(module, stage_classes[stage_number])
        
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(test_class)
        
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        return result.wasSuccessful()
        
    except Exception as e:
        print(f"❌ Failed to run stage {stage_number} tests: {e}")
        return False


def main():
    """Main test runner."""
    if len(sys.argv) > 1:
        # Run specific stage
        try:
            stage = int(sys.argv[1])
            success = run_specific_stage(stage)
        except ValueError:
            print("❌ Invalid stage number. Usage: python run_lifecycle_tests.py [1-7]")
            success = False
    else:
        # Run all tests
        success = run_lifecycle_tests()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()