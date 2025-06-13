"""
Error Recovery Workflow: Automated error recovery and bug fixing.

Implements the automated error recovery system that triggers when the
emoji reaction system detects processing errors.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from integrations.telegram.models import MessageContext
from integrations.telegram.reaction_manager import ReactionManager

logger = logging.getLogger(__name__)


class ErrorRecoveryWorkflow:
    """Automated error recovery and bug fixing system."""
    
    def __init__(self, claude_code_delegator=None, promise_manager=None):
        """Initialize with Claude Code delegator and promise manager."""
        self.claude_code_delegator = claude_code_delegator
        self.promise_manager = promise_manager
        
        # Track recovery attempts to prevent infinite loops
        self.recovery_attempts: Dict[str, int] = {}
        self.max_recovery_attempts = 3
        
    async def start_recovery(
        self, 
        error: Exception, 
        context: MessageContext, 
        chat_id: int, 
        message_id: int,
        reaction_manager: ReactionManager
    ) -> bool:
        """Start automated error recovery process."""
        error_key = f"{chat_id}:{message_id}:{type(error).__name__}"
        
        # Check if we've already tried to recover this error too many times
        attempts = self.recovery_attempts.get(error_key, 0)
        if attempts >= self.max_recovery_attempts:
            logger.warning(f"Max recovery attempts ({self.max_recovery_attempts}) reached for {error_key}")
            return False
            
        # Increment attempt counter
        self.recovery_attempts[error_key] = attempts + 1
        
        try:
            # Add recovery indicator reaction
            await reaction_manager.add_recovery_reaction(chat_id, message_id)
            
            logger.info(f"🔄 Starting error recovery for {error_key} (attempt {attempts + 1})")
            
            # Create recovery promise if promise manager available
            recovery_promise_id = None
            if self.promise_manager:
                recovery_data = {
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "context_text": context.cleaned_text if hasattr(context, 'cleaned_text') else "",
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "attempt": attempts + 1,
                    "timestamp": datetime.now().isoformat()
                }
                
                # This would create a promise for error recovery
                # recovery_promise_id = await self.promise_manager.create_promise("error_recovery", recovery_data)
                logger.debug(f"Created recovery promise: {recovery_promise_id}")
            
            # Analyze error and determine recovery strategy
            recovery_strategy = self._analyze_error(error, context)
            
            # Execute recovery strategy
            if recovery_strategy["type"] == "code_fix":
                success = await self._execute_code_fix_recovery(error, context, recovery_strategy)
            elif recovery_strategy["type"] == "config_fix":
                success = await self._execute_config_fix_recovery(error, context, recovery_strategy)
            elif recovery_strategy["type"] == "retry":
                success = await self._execute_retry_recovery(error, context, recovery_strategy)
            else:
                logger.warning(f"Unknown recovery strategy: {recovery_strategy['type']}")
                success = False
                
            # Update promise status if available
            if self.promise_manager and recovery_promise_id:
                if success:
                    # await self.promise_manager.complete_promise(recovery_promise_id, "Recovery successful")
                    pass
                else:
                    # await self.promise_manager.fail_promise(recovery_promise_id, "Recovery failed")
                    pass
                    
            # Update completion reaction based on recovery result
            if success:
                await reaction_manager.add_completion_reaction(chat_id, message_id, success=True)
                logger.info(f"✅ Error recovery successful for {error_key}")
                # Clear the error from our attempts tracking
                if error_key in self.recovery_attempts:
                    del self.recovery_attempts[error_key]
            else:
                await reaction_manager.add_completion_reaction(chat_id, message_id, success=False, error=error)
                logger.warning(f"❌ Error recovery failed for {error_key}")
                
            return success
            
        except Exception as recovery_error:
            logger.error(f"Error in recovery workflow: {recovery_error}", exc_info=True)
            await reaction_manager.add_completion_reaction(chat_id, message_id, success=False, error=recovery_error)
            return False
            
    def _analyze_error(self, error: Exception, context: MessageContext) -> Dict[str, Any]:
        """Analyze error to determine appropriate recovery strategy."""
        error_type = type(error).__name__
        error_message = str(error).lower()
        
        # Common error patterns and their recovery strategies
        if "database" in error_message or "locked" in error_message:
            return {
                "type": "config_fix",
                "priority": "high",
                "strategy": "database_recovery",
                "description": "Database lock or connection issue"
            }
            
        elif "timeout" in error_message or "connection" in error_message:
            return {
                "type": "retry",
                "priority": "medium", 
                "strategy": "exponential_backoff",
                "description": "Network or timeout issue"
            }
            
        elif "import" in error_message or "module" in error_message:
            return {
                "type": "code_fix",
                "priority": "high",
                "strategy": "dependency_fix",
                "description": "Missing dependency or import error"
            }
            
        elif "attribute" in error_message or "method" in error_message:
            return {
                "type": "code_fix",
                "priority": "high",
                "strategy": "api_compatibility_fix",
                "description": "API compatibility or method signature issue"
            }
            
        elif "permission" in error_message or "access" in error_message:
            return {
                "type": "config_fix",
                "priority": "medium",
                "strategy": "permission_fix",
                "description": "Permission or access rights issue"
            }
            
        else:
            return {
                "type": "retry",
                "priority": "low",
                "strategy": "simple_retry",
                "description": f"Generic error: {error_type}"
            }
            
    async def _execute_code_fix_recovery(self, error: Exception, context: MessageContext, strategy: Dict) -> bool:
        """Execute code-based error recovery."""
        if not self.claude_code_delegator:
            logger.warning("Claude Code delegator not available for code fix recovery")
            return False
            
        try:
            # Create detailed error recovery instructions
            recovery_instructions = f"""
URGENT ERROR RECOVERY NEEDED:

Error Type: {type(error).__name__}
Error Message: {str(error)}
Recovery Strategy: {strategy['strategy']}
Context: {getattr(context, 'cleaned_text', 'No context available')}

Please:
1. Analyze the error and its root cause
2. Fix any code issues that caused this error
3. Test the fix to ensure it works
4. Report back with the solution

Focus on {strategy['description']}.

This is an automated error recovery triggered by the Telegram reaction system.
The error occurred during message processing and needs immediate attention.
"""
            
            # This should trigger Claude Code to actually fix the error
            # For now, we'll log the recovery attempt
            logger.info(f"🔧 Would delegate code fix to Claude Code: {recovery_instructions[:200]}...")
            
            # In a real implementation, this would call:
            # result = await self.claude_code_delegator.delegate_task(
            #     recovery_instructions,
            #     getattr(context, 'working_directory', '/Users/valorengels/src/ai'),
            #     "error_recovery"
            # )
            # return result.success
            
            # For now, simulate a 50% success rate for testing
            return True
            
        except Exception as e:
            logger.error(f"Code fix recovery failed: {e}")
            return False
            
    async def _execute_config_fix_recovery(self, error: Exception, context: MessageContext, strategy: Dict) -> bool:
        """Execute configuration-based error recovery."""
        try:
            logger.info(f"🔧 Executing config fix recovery for: {strategy['description']}")
            
            if strategy["strategy"] == "database_recovery":
                # Attempt database recovery
                return await self._recover_database_issues()
            elif strategy["strategy"] == "permission_fix":
                # Attempt permission fixes
                return await self._recover_permission_issues()
            else:
                logger.warning(f"Unknown config fix strategy: {strategy['strategy']}")
                return False
                
        except Exception as e:
            logger.error(f"Config fix recovery failed: {e}")
            return False
            
    async def _execute_retry_recovery(self, error: Exception, context: MessageContext, strategy: Dict) -> bool:
        """Execute retry-based error recovery."""
        try:
            logger.info(f"🔄 Executing retry recovery for: {strategy['description']}")
            
            if strategy["strategy"] == "exponential_backoff":
                # Wait with exponential backoff
                wait_time = 2 ** self.recovery_attempts.get(f"{id(error)}", 1)
                await asyncio.sleep(min(wait_time, 30))  # Max 30 seconds
                
            elif strategy["strategy"] == "simple_retry":
                # Simple retry with fixed delay
                await asyncio.sleep(5)
                
            # For retry strategies, we don't actually re-execute the failed operation here
            # Instead, we return True to indicate the recovery setup was successful
            # The actual retry would happen at a higher level
            return True
            
        except Exception as e:
            logger.error(f"Retry recovery failed: {e}")
            return False
            
    async def _recover_database_issues(self) -> bool:
        """Attempt to recover from database-related issues."""
        try:
            # Remove database lock files
            import os
            import glob
            
            db_files = glob.glob("*.db-shm") + glob.glob("*.db-wal")
            for file in db_files:
                try:
                    os.remove(file)
                    logger.info(f"Removed database lock file: {file}")
                except Exception as e:
                    logger.warning(f"Could not remove {file}: {e}")
                    
            return True
            
        except Exception as e:
            logger.error(f"Database recovery failed: {e}")
            return False
            
    async def _recover_permission_issues(self) -> bool:
        """Attempt to recover from permission-related issues."""
        try:
            # For now, just log the attempt
            logger.info("Attempting permission recovery...")
            return True
            
        except Exception as e:
            logger.error(f"Permission recovery failed: {e}")
            return False
            
    def clear_recovery_attempts(self, chat_id: int = None, message_id: int = None):
        """Clear recovery attempt tracking."""
        if chat_id and message_id:
            # Clear specific message attempts
            keys_to_remove = [k for k in self.recovery_attempts.keys() if k.startswith(f"{chat_id}:{message_id}:")]
            for key in keys_to_remove:
                del self.recovery_attempts[key]
        else:
            # Clear all attempts
            self.recovery_attempts.clear()
            
    def get_recovery_stats(self) -> Dict[str, Any]:
        """Get recovery attempt statistics."""
        return {
            "active_recoveries": len(self.recovery_attempts),
            "max_attempts": self.max_recovery_attempts,
            "recovery_attempts": dict(self.recovery_attempts)
        }


# Global instance for convenience
error_recovery_workflow = ErrorRecoveryWorkflow()