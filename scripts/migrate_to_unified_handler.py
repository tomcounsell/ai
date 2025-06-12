#!/usr/bin/env python3
"""
Migration script for transitioning to unified message handler.

This script:
1. Creates necessary database tables
2. Validates the new system components
3. Configures feature flags for gradual rollout
4. Provides rollback capability
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utilities.database import get_database_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def create_tables():
    """Create necessary database tables for unified handler."""
    logger.info("Creating database tables...")

    schema_file = project_root / "integrations" / "telegram" / "database_schema.sql"

    if not schema_file.exists():
        logger.error(f"Schema file not found: {schema_file}")
        return False

    try:
        with get_database_connection() as conn:
            # Read and execute schema
            with open(schema_file) as f:
                schema = f.read()

            conn.executescript(schema)
            conn.commit()

        logger.info("✅ Database tables created successfully")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to create tables: {str(e)}")
        return False


def validate_components():
    """Validate that all unified handler components are importable."""
    logger.info("Validating unified handler components...")

    components = [
        "integrations.telegram.models",
        "integrations.telegram.components",
        "integrations.telegram.unified_processor",
        "integrations.telegram.handlers_unified",
        "integrations.telegram.error_management",
    ]

    all_valid = True

    for component in components:
        try:
            __import__(component)
            logger.info(f"✅ {component}")
        except ImportError as e:
            logger.error(f"❌ {component}: {str(e)}")
            all_valid = False

    return all_valid


def set_feature_flags(rollout_percentage: int = 0):
    """Configure feature flags for gradual rollout."""
    logger.info(f"Setting feature flags for {rollout_percentage}% rollout...")

    try:
        with get_database_connection() as conn:
            # Update unified processor flag
            conn.execute(
                """
                UPDATE feature_flags
                SET enabled = ?, rollout_percentage = ?, updated_at = datetime('now')
                WHERE flag_name = 'unified_message_processor'
            """,
                (rollout_percentage > 0, rollout_percentage),
            )

            # Enable legacy fallback for safety during rollout
            conn.execute(
                """
                UPDATE feature_flags
                SET enabled = ?, updated_at = datetime('now')
                WHERE flag_name = 'legacy_fallback'
            """,
                (rollout_percentage < 100,),
            )

            conn.commit()

        logger.info(f"✅ Feature flags updated for {rollout_percentage}% rollout")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to update feature flags: {str(e)}")
        return False


def run_health_check():
    """Run health check on unified processor."""
    logger.info("Running health check...")

    try:
        from integrations.telegram.unified_processor import UnifiedMessageProcessor

        processor = UnifiedMessageProcessor()

        # Test component initialization
        if hasattr(processor, "security_gate") and hasattr(processor, "context_builder"):
            logger.info("✅ Components initialized successfully")
        else:
            logger.error("❌ Missing required components")
            return False

        # Test metrics
        metrics = processor.get_metrics()
        logger.info(f"✅ Metrics system working: {metrics}")

        return True

    except Exception as e:
        logger.error(f"❌ Health check failed: {str(e)}")
        return False


def show_migration_status():
    """Display current migration status."""
    logger.info("\n=== Migration Status ===")

    try:
        with get_database_connection() as conn:
            # Check feature flags
            cursor = conn.execute("""
                SELECT flag_name, enabled, rollout_percentage, updated_at
                FROM feature_flags
                WHERE flag_name IN ('unified_message_processor', 'legacy_fallback')
            """)

            flags = cursor.fetchall()

            logger.info("\nFeature Flags:")
            for flag in flags:
                status = "ENABLED" if flag[1] else "DISABLED"
                logger.info(f"  {flag[0]}: {status} ({flag[2]}%) - Updated: {flag[3]}")

            # Check message metrics
            cursor = conn.execute("""
                SELECT COUNT(*) as days, SUM(success_count) as total_success, SUM(error_count) as total_errors
                FROM message_metrics
            """)

            metrics = cursor.fetchone()
            if metrics[0] > 0:
                logger.info("\nMessage Metrics:")
                logger.info(f"  Days tracked: {metrics[0]}")
                logger.info(f"  Total successful: {metrics[1]}")
                logger.info(f"  Total errors: {metrics[2]}")
                error_rate = (
                    metrics[2] / (metrics[1] + metrics[2]) * 100
                    if (metrics[1] + metrics[2]) > 0
                    else 0
                )
                logger.info(f"  Error rate: {error_rate:.2f}%")

    except Exception as e:
        logger.error(f"Failed to get status: {str(e)}")


def rollback():
    """Rollback to legacy handler."""
    logger.info("Rolling back to legacy handler...")

    if set_feature_flags(0):
        logger.info("✅ Rollback completed - legacy handler active")
        return True
    else:
        logger.error("❌ Rollback failed")
        return False


def main():
    parser = argparse.ArgumentParser(description="Migrate to unified message handler")
    parser.add_argument("--rollout", type=int, default=0, help="Rollout percentage (0-100)")
    parser.add_argument("--rollback", action="store_true", help="Rollback to legacy handler")
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument("--skip-validation", action="store_true", help="Skip component validation")

    args = parser.parse_args()

    if args.status:
        show_migration_status()
        return

    if args.rollback:
        if rollback():
            sys.exit(0)
        else:
            sys.exit(1)

    # Run migration steps
    logger.info("Starting migration to unified message handler...")

    # Step 1: Create tables
    if not create_tables():
        logger.error("Migration failed at database setup")
        sys.exit(1)

    # Step 2: Validate components
    if not args.skip_validation:
        if not validate_components():
            logger.error("Migration failed at component validation")
            sys.exit(1)

    # Step 3: Run health check
    if not run_health_check():
        logger.error("Migration failed at health check")
        sys.exit(1)

    # Step 4: Set feature flags
    if not set_feature_flags(args.rollout):
        logger.error("Migration failed at feature flag configuration")
        sys.exit(1)

    # Show final status
    show_migration_status()

    logger.info("\n✅ Migration completed successfully!")
    logger.info(f"Unified handler active for {args.rollout}% of traffic")

    if args.rollout < 100:
        logger.info(
            f"\nTo increase rollout: python scripts/migrate_to_unified_handler.py --rollout {min(args.rollout + 10, 100)}"
        )

    if args.rollout > 0:
        logger.info(
            "\nTo rollback if needed: python scripts/migrate_to_unified_handler.py --rollback"
        )


if __name__ == "__main__":
    main()
