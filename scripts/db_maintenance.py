"""
Automated database maintenance for production systems.
Handles backups, vacuuming, index optimization, and health checks.
"""

import os
import sys
import sqlite3
import shutil
import time
import logging
import json
import gzip
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

class DatabaseMaintenance:
    """
    Automated database maintenance system.
    
    Features:
    - Automated backups with compression
    - Database optimization (VACUUM, ANALYZE)
    - Index optimization
    - Integrity checks
    - Performance monitoring
    - Cleanup of old data
    """
    
    def __init__(self, db_path: str = "data/ai_rebuild.db", backup_dir: str = "data/backups"):
        self.db_path = db_path
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(__name__)
        self._setup_logging()
        
        # Maintenance configuration
        self.config = {
            'backup_retention_days': 30,
            'vacuum_threshold_percent': 10,
            'max_backup_size_mb': 1000,
            'compress_backups': True,
            'integrity_check_frequency': 7,  # days
            'analyze_threshold_changes': 1000  # row changes before ANALYZE
        }
    
    def _setup_logging(self):
        """Setup maintenance logging"""
        handler = logging.FileHandler('logs/db_maintenance.log')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
    
    def run_full_maintenance(self) -> Dict[str, Any]:
        """Run complete database maintenance routine"""
        start_time = datetime.now()
        results = {
            'start_time': start_time.isoformat(),
            'tasks': {},
            'success': True,
            'errors': []
        }
        
        self.logger.info("Starting full database maintenance")
        
        maintenance_tasks = [
            ('integrity_check', self.integrity_check),
            ('create_backup', self.create_backup),
            ('vacuum_database', self.vacuum_database),
            ('analyze_database', self.analyze_database),
            ('optimize_indexes', self.optimize_indexes),
            ('cleanup_old_data', self.cleanup_old_data),
            ('cleanup_old_backups', self.cleanup_old_backups),
            ('collect_statistics', self.collect_statistics)
        ]
        
        for task_name, task_func in maintenance_tasks:
            try:
                self.logger.info(f"Running task: {task_name}")
                task_start = time.time()
                
                task_result = task_func()
                task_duration = time.time() - task_start
                
                results['tasks'][task_name] = {
                    'success': True,
                    'duration_seconds': task_duration,
                    'result': task_result
                }
                
                self.logger.info(f"Completed task: {task_name} ({task_duration:.2f}s)")
                
            except Exception as e:
                task_duration = time.time() - task_start
                error_msg = f"Task {task_name} failed: {str(e)}"
                
                results['tasks'][task_name] = {
                    'success': False,
                    'duration_seconds': task_duration,
                    'error': str(e)
                }
                
                results['errors'].append(error_msg)
                results['success'] = False
                
                self.logger.error(error_msg)
        
        total_duration = (datetime.now() - start_time).total_seconds()
        results['total_duration_seconds'] = total_duration
        results['end_time'] = datetime.now().isoformat()
        
        if results['success']:
            self.logger.info(f"Database maintenance completed successfully ({total_duration:.2f}s)")
        else:
            self.logger.error(f"Database maintenance completed with errors ({total_duration:.2f}s)")
        
        return results
    
    def create_backup(self) -> Dict[str, Any]:
        """Create database backup"""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"ai_rebuild_backup_{timestamp}.db"
        backup_path = self.backup_dir / backup_name
        
        # Create backup
        start_size = os.path.getsize(self.db_path)
        shutil.copy2(self.db_path, backup_path)
        
        result = {
            'backup_path': str(backup_path),
            'original_size_mb': start_size / (1024 * 1024),
            'backup_size_mb': os.path.getsize(backup_path) / (1024 * 1024),
            'compressed': False
        }
        
        # Compress backup if configured
        if self.config['compress_backups']:
            compressed_path = backup_path.with_suffix('.db.gz')
            
            with open(backup_path, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            # Remove uncompressed backup
            backup_path.unlink()
            
            result.update({
                'backup_path': str(compressed_path),
                'backup_size_mb': os.path.getsize(compressed_path) / (1024 * 1024),
                'compressed': True,
                'compression_ratio': os.path.getsize(compressed_path) / start_size
            })
        
        self.logger.info(f"Backup created: {result['backup_path']} ({result['backup_size_mb']:.2f}MB)")
        return result
    
    def vacuum_database(self) -> Dict[str, Any]:
        """Vacuum database to reclaim space"""
        # Check if vacuum is needed
        db_stats = self.get_database_stats()
        page_count = db_stats.get('page_count', 0)
        freelist_count = db_stats.get('freelist_count', 0)
        
        if page_count == 0:
            return {'skipped': True, 'reason': 'Empty database'}
        
        fragmentation_percent = (freelist_count / page_count) * 100 if page_count > 0 else 0
        
        if fragmentation_percent < self.config['vacuum_threshold_percent']:
            return {
                'skipped': True,
                'reason': f'Fragmentation {fragmentation_percent:.1f}% below threshold {self.config["vacuum_threshold_percent"]}%',
                'fragmentation_percent': fragmentation_percent
            }
        
        # Perform vacuum
        start_size = os.path.getsize(self.db_path)
        start_time = time.time()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('VACUUM')
        
        end_size = os.path.getsize(self.db_path)
        duration = time.time() - start_time
        space_saved = start_size - end_size
        
        result = {
            'fragmentation_percent': fragmentation_percent,
            'start_size_mb': start_size / (1024 * 1024),
            'end_size_mb': end_size / (1024 * 1024),
            'space_saved_mb': space_saved / (1024 * 1024),
            'duration_seconds': duration
        }
        
        self.logger.info(f"VACUUM completed: saved {result['space_saved_mb']:.2f}MB ({duration:.2f}s)")
        return result
    
    def analyze_database(self) -> Dict[str, Any]:
        """Analyze database to update statistics"""
        start_time = time.time()
        
        with sqlite3.connect(self.db_path) as conn:
            # Get table list
            tables = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """).fetchall()
            
            analyzed_tables = []
            
            for (table_name,) in tables:
                # Analyze each table
                conn.execute(f'ANALYZE {table_name}')
                analyzed_tables.append(table_name)
        
        duration = time.time() - start_time
        
        result = {
            'analyzed_tables': analyzed_tables,
            'table_count': len(analyzed_tables),
            'duration_seconds': duration
        }
        
        self.logger.info(f"ANALYZE completed: {len(analyzed_tables)} tables ({duration:.2f}s)")
        return result
    
    def optimize_indexes(self) -> Dict[str, Any]:
        """Optimize database indexes"""
        start_time = time.time()
        
        with sqlite3.connect(self.db_path) as conn:
            # Get index information
            indexes = conn.execute("""
                SELECT name, tbl_name FROM sqlite_master 
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
            """).fetchall()
            
            optimized_indexes = []
            
            for index_name, table_name in indexes:
                try:
                    # Reindex to optimize
                    conn.execute(f'REINDEX {index_name}')
                    optimized_indexes.append({
                        'index_name': index_name,
                        'table_name': table_name
                    })
                except sqlite3.Error as e:
                    self.logger.warning(f"Failed to reindex {index_name}: {e}")
        
        duration = time.time() - start_time
        
        result = {
            'optimized_indexes': optimized_indexes,
            'index_count': len(optimized_indexes),
            'duration_seconds': duration
        }
        
        self.logger.info(f"Index optimization completed: {len(optimized_indexes)} indexes ({duration:.2f}s)")
        return result
    
    def integrity_check(self) -> Dict[str, Any]:
        """Check database integrity"""
        start_time = time.time()
        
        with sqlite3.connect(self.db_path) as conn:
            # Quick integrity check
            quick_check = conn.execute('PRAGMA quick_check').fetchone()[0]
            
            integrity_result = {
                'quick_check': quick_check,
                'quick_check_passed': quick_check == 'ok'
            }
            
            # Full integrity check if quick check fails or periodically
            if quick_check != 'ok' or self._should_run_full_integrity_check():
                self.logger.info("Running full integrity check")
                full_check_results = conn.execute('PRAGMA integrity_check').fetchall()
                
                integrity_result.update({
                    'full_check_performed': True,
                    'full_check_results': [result[0] for result in full_check_results],
                    'full_check_passed': len(full_check_results) == 1 and full_check_results[0][0] == 'ok'
                })
            else:
                integrity_result['full_check_performed'] = False
        
        duration = time.time() - start_time
        integrity_result['duration_seconds'] = duration
        
        if integrity_result['quick_check_passed']:
            self.logger.info(f"Integrity check passed ({duration:.2f}s)")
        else:
            self.logger.error(f"Integrity check FAILED ({duration:.2f}s)")
        
        return integrity_result
    
    def cleanup_old_data(self) -> Dict[str, Any]:
        """Clean up old data based on retention policies"""
        start_time = time.time()
        cleaned_records = {}
        
        # Define cleanup policies
        cleanup_policies = [
            ('resource_metrics', 'timestamp', 30),  # 30 days
            ('alerts', 'timestamp', 90),  # 90 days  
            ('restart_events', 'timestamp', 60),  # 60 days
            ('alert_history', 'timestamp', 90)  # 90 days
        ]
        
        with sqlite3.connect(self.db_path) as conn:
            for table_name, date_column, retention_days in cleanup_policies:
                try:
                    cutoff_date = datetime.now() - timedelta(days=retention_days)
                    
                    # Count records to be deleted
                    count_query = f"""
                        SELECT COUNT(*) FROM {table_name} 
                        WHERE {date_column} < ?
                    """
                    old_count = conn.execute(count_query, (cutoff_date,)).fetchone()[0]
                    
                    if old_count > 0:
                        # Delete old records
                        delete_query = f"""
                            DELETE FROM {table_name} 
                            WHERE {date_column} < ?
                        """
                        conn.execute(delete_query, (cutoff_date,))
                        
                        cleaned_records[table_name] = {
                            'deleted_count': old_count,
                            'retention_days': retention_days,
                            'cutoff_date': cutoff_date.isoformat()
                        }
                        
                        self.logger.info(f"Cleaned {old_count} old records from {table_name}")
                    else:
                        cleaned_records[table_name] = {'deleted_count': 0}
                        
                except sqlite3.Error as e:
                    self.logger.warning(f"Failed to clean {table_name}: {e}")
                    cleaned_records[table_name] = {'error': str(e)}
            
            conn.commit()
        
        duration = time.time() - start_time
        total_deleted = sum(
            info.get('deleted_count', 0) for info in cleaned_records.values()
            if isinstance(info.get('deleted_count'), int)
        )
        
        result = {
            'cleaned_tables': cleaned_records,
            'total_deleted_records': total_deleted,
            'duration_seconds': duration
        }
        
        self.logger.info(f"Data cleanup completed: {total_deleted} records deleted ({duration:.2f}s)")
        return result
    
    def cleanup_old_backups(self) -> Dict[str, Any]:
        """Clean up old backup files"""
        if not self.backup_dir.exists():
            return {'deleted_count': 0, 'reason': 'Backup directory does not exist'}
        
        retention_days = self.config['backup_retention_days']
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        
        deleted_backups = []
        total_size_saved = 0
        
        for backup_file in self.backup_dir.glob('ai_rebuild_backup_*.db*'):
            try:
                file_mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
                
                if file_mtime < cutoff_date:
                    file_size = backup_file.stat().st_size
                    backup_file.unlink()
                    
                    deleted_backups.append({
                        'filename': backup_file.name,
                        'size_mb': file_size / (1024 * 1024),
                        'date': file_mtime.isoformat()
                    })
                    
                    total_size_saved += file_size
                    
            except Exception as e:
                self.logger.warning(f"Failed to process backup {backup_file}: {e}")
        
        result = {
            'deleted_backups': deleted_backups,
            'deleted_count': len(deleted_backups),
            'total_size_saved_mb': total_size_saved / (1024 * 1024),
            'retention_days': retention_days
        }
        
        self.logger.info(f"Backup cleanup: {len(deleted_backups)} old backups deleted, {result['total_size_saved_mb']:.2f}MB saved")
        return result
    
    def collect_statistics(self) -> Dict[str, Any]:
        """Collect database statistics"""
        stats = self.get_database_stats()
        
        with sqlite3.connect(self.db_path) as conn:
            # Table statistics
            table_stats = {}
            tables = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """).fetchall()
            
            for (table_name,) in tables:
                try:
                    row_count = conn.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()[0]
                    table_stats[table_name] = {'row_count': row_count}
                except sqlite3.Error as e:
                    table_stats[table_name] = {'error': str(e)}
            
            # Index statistics
            index_count = conn.execute("""
                SELECT COUNT(*) FROM sqlite_master 
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
            """).fetchone()[0]
        
        result = {
            'database_stats': stats,
            'table_stats': table_stats,
            'index_count': index_count,
            'collection_time': datetime.now().isoformat()
        }
        
        self.logger.info(f"Statistics collected for {len(table_stats)} tables")
        return result
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Get basic database statistics"""
        if not os.path.exists(self.db_path):
            return {}
        
        file_size = os.path.getsize(self.db_path)
        
        with sqlite3.connect(self.db_path) as conn:
            # Database pragmas
            page_count = conn.execute('PRAGMA page_count').fetchone()[0]
            page_size = conn.execute('PRAGMA page_size').fetchone()[0]
            freelist_count = conn.execute('PRAGMA freelist_count').fetchone()[0]
            schema_version = conn.execute('PRAGMA schema_version').fetchone()[0]
            user_version = conn.execute('PRAGMA user_version').fetchone()[0]
        
        return {
            'file_size_bytes': file_size,
            'file_size_mb': file_size / (1024 * 1024),
            'page_count': page_count,
            'page_size': page_size,
            'freelist_count': freelist_count,
            'fragmentation_percent': (freelist_count / page_count) * 100 if page_count > 0 else 0,
            'schema_version': schema_version,
            'user_version': user_version
        }
    
    def _should_run_full_integrity_check(self) -> bool:
        """Determine if full integrity check should be run"""
        integrity_file = Path('data/last_integrity_check.txt')
        
        if not integrity_file.exists():
            return True
        
        try:
            last_check = datetime.fromisoformat(integrity_file.read_text().strip())
            days_since_check = (datetime.now() - last_check).days
            
            if days_since_check >= self.config['integrity_check_frequency']:
                # Update the integrity check file
                integrity_file.write_text(datetime.now().isoformat())
                return True
                
        except Exception:
            return True
        
        return False

def main():
    """Main database maintenance function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='AI Rebuild Database Maintenance')
    parser.add_argument('--db-path', default='data/ai_rebuild.db', help='Database file path')
    parser.add_argument('--backup-dir', default='data/backups', help='Backup directory')
    parser.add_argument('--task', choices=['backup', 'vacuum', 'analyze', 'integrity', 'cleanup', 'full'],
                       default='full', help='Specific maintenance task')
    parser.add_argument('--output', help='Output results to JSON file')
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    maintenance = DatabaseMaintenance(args.db_path, args.backup_dir)
    
    try:
        if args.task == 'backup':
            result = maintenance.create_backup()
        elif args.task == 'vacuum':
            result = maintenance.vacuum_database()
        elif args.task == 'analyze':
            result = maintenance.analyze_database()
        elif args.task == 'integrity':
            result = maintenance.integrity_check()
        elif args.task == 'cleanup':
            result = maintenance.cleanup_old_data()
        else:  # full
            result = maintenance.run_full_maintenance()
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(result, f, indent=2)
            print(f"Results saved to {args.output}")
        else:
            print(json.dumps(result, indent=2))
        
        return 0 if result.get('success', True) else 1
        
    except Exception as e:
        logging.error(f"Database maintenance failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())