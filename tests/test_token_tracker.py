"""Tests for token usage tracking system."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities.token_tracker import TokenTracker, log_token_usage, get_tracker


class TestTokenTracker(unittest.TestCase):
    """Test cases for TokenTracker class."""
    
    def setUp(self):
        """Set up test database."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        self.tracker = TokenTracker(self.temp_db.name)
    
    def tearDown(self):
        """Clean up test database."""
        os.unlink(self.temp_db.name)
    
    def test_database_initialization(self):
        """Test that database is properly initialized."""
        # Check that tables exist by calling init (should not raise an error)
        self.tracker._init_database()
        
        # Verify default data exists
        summary = self.tracker.get_usage_summary()
        self.assertEqual(summary["request_count"], 0)
    
    def test_log_usage_basic(self):
        """Test basic token usage logging."""
        record_id = self.tracker.log_usage(
            project="test_project",
            host="OpenAI",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50
        )
        
        self.assertIsInstance(record_id, int)
        self.assertGreater(record_id, 0)
        
        # Verify usage was logged
        summary = self.tracker.get_usage_summary()
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["total_input_tokens"], 100)
        self.assertEqual(summary["total_output_tokens"], 50)
        self.assertEqual(summary["total_tokens"], 150)
    
    def test_log_usage_with_cost_calculation(self):
        """Test that cost is calculated correctly for known models."""
        self.tracker.log_usage(
            project="test_project",
            host="OpenAI",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=1000
        )
        
        summary = self.tracker.get_usage_summary()
        # gpt-4o: $0.005 input, $0.015 output per 1k tokens
        expected_cost = (1000 * 0.005 / 1000) + (1000 * 0.015 / 1000)
        self.assertAlmostEqual(summary["total_cost_usd"], expected_cost, places=4)
    
    def test_log_usage_unknown_model(self):
        """Test logging with unknown model."""
        record_id = self.tracker.log_usage(
            project="test_project",
            host="CustomProvider",
            model="custom-model",
            input_tokens=100,
            output_tokens=50
        )
        
        self.assertIsInstance(record_id, int)
        
        # Cost should be None for unknown models
        summary = self.tracker.get_usage_summary(host="CustomProvider")
        self.assertEqual(summary["total_cost_usd"], 0.0)
    
    def test_multiple_projects(self):
        """Test usage tracking across multiple projects."""
        self.tracker.log_usage("project_a", "OpenAI", "gpt-4o", 100, 50)
        self.tracker.log_usage("project_b", "OpenAI", "gpt-4o", 200, 100)
        self.tracker.log_usage("project_a", "Anthropic", "claude-3-opus-20240229", 150, 75)
        
        # Test project-specific summaries
        project_a_summary = self.tracker.get_usage_summary(project="project_a")
        self.assertEqual(project_a_summary["request_count"], 2)
        self.assertEqual(project_a_summary["total_tokens"], 375)
        
        project_b_summary = self.tracker.get_usage_summary(project="project_b")
        self.assertEqual(project_b_summary["request_count"], 1)
        self.assertEqual(project_b_summary["total_tokens"], 300)
    
    def test_usage_by_project(self):
        """Test get_usage_by_project method."""
        self.tracker.log_usage("project_a", "OpenAI", "gpt-4o", 100, 50)
        self.tracker.log_usage("project_b", "OpenAI", "gpt-4o", 200, 100)
        self.tracker.log_usage("project_a", "OpenAI", "gpt-4o", 50, 25)
        
        projects = self.tracker.get_usage_by_project()
        
        self.assertEqual(len(projects), 2)
        
        # Should be sorted by total tokens descending
        self.assertEqual(projects[0]["project"], "project_b")
        self.assertEqual(projects[0]["total_tokens"], 300)
        self.assertEqual(projects[1]["project"], "project_a")
        self.assertEqual(projects[1]["total_tokens"], 225)
    
    def test_usage_by_model(self):
        """Test get_usage_by_model method."""
        self.tracker.log_usage("test", "OpenAI", "gpt-4o", 100, 50)
        self.tracker.log_usage("test", "OpenAI", "gpt-4o-mini", 200, 100)
        self.tracker.log_usage("test", "Anthropic", "claude-3-opus-20240229", 150, 75)
        
        models = self.tracker.get_usage_by_model()
        
        self.assertEqual(len(models), 3)
        
        # Find specific model
        gpt4o_mini = next(m for m in models if m["model"] == "gpt-4o-mini")
        self.assertEqual(gpt4o_mini["host"], "OpenAI")
        self.assertEqual(gpt4o_mini["total_tokens"], 300)
    
    def test_date_filtering(self):
        """Test date-based filtering."""
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        
        # Log usage for yesterday
        self.tracker.log_usage(
            "test", "OpenAI", "gpt-4o", 100, 50, timestamp=yesterday
        )
        
        # Log usage for today
        self.tracker.log_usage(
            "test", "OpenAI", "gpt-4o", 200, 100, timestamp=now
        )
        
        # Test filtering for today only
        today_summary = self.tracker.get_usage_summary(
            start_date=now.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        self.assertEqual(today_summary["total_tokens"], 300)
        
        # Test filtering for yesterday only
        yesterday_summary = self.tracker.get_usage_summary(
            start_date=yesterday.replace(hour=0, minute=0, second=0, microsecond=0),
            end_date=yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        )
        self.assertEqual(yesterday_summary["total_tokens"], 150)
    
    def test_daily_usage(self):
        """Test get_daily_usage method."""
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        
        self.tracker.log_usage("test", "OpenAI", "gpt-4o", 100, 50, timestamp=yesterday)
        self.tracker.log_usage("test", "OpenAI", "gpt-4o", 200, 100, timestamp=now)
        
        daily_data = self.tracker.get_daily_usage(days=2)
        
        self.assertGreaterEqual(len(daily_data), 1)
        self.assertLessEqual(len(daily_data), 2)
    
    def test_export_data(self):
        """Test data export functionality."""
        self.tracker.log_usage("test", "OpenAI", "gpt-4o", 100, 50)
        
        csv_data = self.tracker.export_usage_data(format="csv")
        
        self.assertIn("timestamp,project,host,model", csv_data)
        self.assertIn("test,OpenAI,gpt-4o", csv_data)
        self.assertIn("100,50,150", csv_data)
    
    def test_user_filtering(self):
        """Test filtering by user ID."""
        self.tracker.log_usage("test", "OpenAI", "gpt-4o", 100, 50, user_id="user1")
        self.tracker.log_usage("test", "OpenAI", "gpt-4o", 200, 100, user_id="user2")
        
        user1_summary = self.tracker.get_usage_summary(user_id="user1")
        self.assertEqual(user1_summary["total_tokens"], 150)
        
        user2_summary = self.tracker.get_usage_summary(user_id="user2")
        self.assertEqual(user2_summary["total_tokens"], 300)
    
    def test_request_id_tracking(self):
        """Test request ID tracking."""
        record_id = self.tracker.log_usage(
            "test", "OpenAI", "gpt-4o", 100, 50, request_id="req_123"
        )
        
        self.assertIsInstance(record_id, int)
        
        # Verify we can query by the logged data
        summary = self.tracker.get_usage_summary()
        self.assertEqual(summary["request_count"], 1)


class TestConvenienceFunctions(unittest.TestCase):
    """Test convenience functions."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
    
    def tearDown(self):
        """Clean up test environment."""
        os.unlink(self.temp_db.name)
    
    @patch('utilities.token_tracker._tracker', None)
    def test_log_token_usage_function(self):
        """Test the convenience log_token_usage function."""
        with patch('utilities.token_tracker.get_tracker') as mock_get_tracker:
            mock_tracker = TokenTracker(self.temp_db.name)
            mock_get_tracker.return_value = mock_tracker
            
            record_id = log_token_usage(
                project="test",
                host="OpenAI",
                model="gpt-4o",
                input_tokens=100,
                output_tokens=50
            )
            
            self.assertIsInstance(record_id, int)
            mock_get_tracker.assert_called_once()
    
    @patch('utilities.token_tracker._tracker', None)
    def test_get_tracker_singleton(self):
        """Test that get_tracker returns singleton instance."""
        with patch('utilities.token_tracker.TokenTracker') as mock_tracker_class:
            mock_instance = mock_tracker_class.return_value
            
            # First call should create instance
            tracker1 = get_tracker(self.temp_db.name)
            self.assertEqual(tracker1, mock_instance)
            
            # Second call should return same instance
            tracker2 = get_tracker()
            self.assertEqual(tracker2, mock_instance)
            
            # Should only create one instance
            mock_tracker_class.assert_called_once_with(self.temp_db.name)


class TestErrorHandling(unittest.TestCase):
    """Test error handling scenarios."""
    
    def test_invalid_db_path(self):
        """Test handling of invalid database path."""
        # Should create directory if it doesn't exist
        invalid_path = "/tmp/nonexistent_dir/test.db"
        tracker = TokenTracker(invalid_path)
        
        # Should be able to log usage
        record_id = tracker.log_usage("test", "OpenAI", "gpt-4o", 100, 50)
        self.assertIsInstance(record_id, int)
        
        # Clean up
        os.unlink(invalid_path)
        os.rmdir(os.path.dirname(invalid_path))
    
    def test_zero_tokens(self):
        """Test handling of zero token counts."""
        temp_db = tempfile.NamedTemporaryFile(delete=False)
        temp_db.close()
        
        try:
            tracker = TokenTracker(temp_db.name)
            record_id = tracker.log_usage("test", "OpenAI", "gpt-4o", 0, 0)
            self.assertIsInstance(record_id, int)
            
            summary = tracker.get_usage_summary()
            self.assertEqual(summary["total_tokens"], 0)
        finally:
            os.unlink(temp_db.name)
    
    def test_negative_tokens(self):
        """Test handling of negative token counts."""
        temp_db = tempfile.NamedTemporaryFile(delete=False)
        temp_db.close()
        
        try:
            tracker = TokenTracker(temp_db.name)
            # This should work but might not be realistic
            record_id = tracker.log_usage("test", "OpenAI", "gpt-4o", -10, 50)
            self.assertIsInstance(record_id, int)
        finally:
            os.unlink(temp_db.name)


if __name__ == "__main__":
    unittest.main()