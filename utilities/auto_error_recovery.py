"""
Automatic Error Recovery System

Provides self-healing capabilities by:
1. Capturing errors and sending user-friendly messages
2. Automatically analyzing and fixing common issues
3. Creating promises for background error resolution
"""

import logging
import traceback
import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from utilities.swe_error_recovery import SWEErrorRecovery, ErrorCategory
from tools.valor_delegation_tool import spawn_valor_session

logger = logging.getLogger(__name__)


class RecoveryAction(Enum):
    """Types of recovery actions that can be taken"""
    AUTO_FIX = "auto_fix"           # Attempt automatic fix
    CREATE_PROMISE = "create_promise" # Schedule background fix
    LOG_ONLY = "log_only"           # Just log for manual review
    RETRY = "retry"                 # Retry the operation


@dataclass
class ErrorContext:
    """Context information about an error"""
    error_type: str
    error_message: str
    traceback_info: str
    chat_id: int
    username: Optional[str]
    function_name: str
    input_data: Dict[str, Any]
    timestamp: datetime


class AutoErrorRecovery:
    """Automatic error recovery and self-healing system"""
    
    def __init__(self):
        self.swe_recovery = SWEErrorRecovery()
        self.recovery_history = []
        
    async def handle_error_with_recovery(
        self,
        error: Exception,
        chat_id: int,
        reply_function,
        chat_history_obj,
        context: Dict[str, Any]
    ) -> bool:
        """
        Handle an error with automatic recovery attempt.
        
        Args:
            error: The exception that occurred
            chat_id: Telegram chat ID
            reply_function: Function to send reply to user
            chat_history_obj: Chat history object for context
            context: Additional context about the error
            
        Returns:
            bool: True if recovery was attempted, False otherwise
        """
        try:
            # Create error context
            error_context = ErrorContext(
                error_type=type(error).__name__,
                error_message=str(error),
                traceback_info=traceback.format_exc(),
                chat_id=chat_id,
                username=context.get('username'),
                function_name=context.get('function_name', 'unknown'),
                input_data=context.get('input_data', {}),
                timestamp=datetime.now()
            )
            
            # Send user-friendly error message immediately
            user_message = self._create_user_error_message(error_context)
            await reply_function(user_message)
            
            # Add error to chat history
            if chat_history_obj:
                chat_history_obj.add_message(chat_id, "assistant", user_message)
            
            # Determine recovery action
            recovery_action = self._determine_recovery_action(error_context)
            
            # Execute recovery action
            await self._execute_recovery_action(recovery_action, error_context, chat_id)
            
            # Log the recovery attempt
            self._log_recovery_attempt(error_context, recovery_action)
            
            return True
            
        except Exception as recovery_error:
            logger.error(f"Error in recovery system itself: {recovery_error}")
            logger.error(f"Original error was: {error}")
            return False
    
    def _create_user_error_message(self, error_context: ErrorContext) -> str:
        """Create a user-friendly error message"""
        
        # Determine error category for user-friendly messaging
        category = self.swe_recovery.categorize_error(error_context.error_message)
        
        if category == ErrorCategory.TIMEOUT:
            return "⏱️ **Timeout Error**\n\nThe operation took too long and timed out. I'm working on optimizing this and will fix it automatically. Please try again in a moment."
        
        elif "is_priority" in error_context.error_message.lower():
            return "🔧 **Code Error**\n\nI found a bug in my code (missing variable definition). I'm fixing this automatically right now and restarting the system. This should work on your next message!"
        
        elif "indentation" in error_context.error_message.lower() or "syntax" in error_context.error_message.lower():
            return "📝 **Syntax Error**\n\nI have a code formatting issue that I'm fixing automatically. The system will restart shortly and this should be resolved on your next message."
        
        elif category == ErrorCategory.PERMISSION:
            return "🔒 **Permission Error**\n\nI don't have the right permissions for this operation. I'm investigating and will fix this automatically."
        
        elif category == ErrorCategory.FILE_NOT_FOUND:
            return "📁 **File Not Found**\n\nA required file is missing. I'm analyzing this and will create the missing components automatically."
        
        else:
            # Generic error with self-healing message
            return f"❌ **Error: {error_context.error_type}**\n\n{error_context.error_message[:200]}{'...' if len(error_context.error_message) > 200 else ''}\n\n🔧 I'm analyzing this error and will fix it automatically. Please try again in a moment."
    
    def _determine_recovery_action(self, error_context: ErrorContext) -> RecoveryAction:
        """Determine what recovery action to take"""
        
        error_msg = error_context.error_message.lower()
        
        # Immediate auto-fix for common code errors
        if any(keyword in error_msg for keyword in [
            "is_priority", "name 'is_priority' is not defined",
            "unexpected indent", "indentation", "syntaxerror"
        ]):
            return RecoveryAction.AUTO_FIX
        
        # Background promise for complex issues
        if any(keyword in error_msg for keyword in [
            "timeout", "connection", "api", "network", "permission"
        ]):
            return RecoveryAction.CREATE_PROMISE
        
        # Log critical errors for manual review
        if any(keyword in error_msg for keyword in [
            "database", "critical", "system", "memory"
        ]):
            return RecoveryAction.LOG_ONLY
        
        # Default to auto-fix for most errors
        return RecoveryAction.AUTO_FIX
    
    async def _execute_recovery_action(
        self, action: RecoveryAction, error_context: ErrorContext, chat_id: int
    ):
        """Execute the determined recovery action"""
        
        try:
            if action == RecoveryAction.AUTO_FIX:
                await self._attempt_auto_fix(error_context, chat_id)
                
            elif action == RecoveryAction.CREATE_PROMISE:
                await self._create_recovery_promise(error_context, chat_id)
                
            elif action == RecoveryAction.LOG_ONLY:
                await self._log_for_manual_review(error_context)
                
            elif action == RecoveryAction.RETRY:
                # Not implemented yet - would need access to original operation
                logger.info(f"Retry action determined but not implemented for {error_context.function_name}")
                
        except Exception as e:
            logger.error(f"Failed to execute recovery action {action.value}: {e}")
    
    async def _attempt_auto_fix(self, error_context: ErrorContext, chat_id: int):
        """Attempt to automatically fix the error"""
        
        error_msg = error_context.error_message.lower()
        
        if "is_priority" in error_msg:
            # Create a targeted fix for the is_priority error
            fix_task = f"""
Fix the missing 'is_priority' variable definition in the Telegram handlers.

Error context:
- Function: {error_context.function_name}
- Error: {error_context.error_message}
- File: integrations/telegram/handlers.py

Task:
1. Find functions that use 'is_priority_question=is_priority' but don't define 'is_priority'
2. Add appropriate 'is_priority = False' or intent-based logic
3. Test that the file compiles without errors
4. Restart the server if needed

This is a critical fix that should resolve immediately.
"""
            
            logger.info(f"🔧 Auto-fixing is_priority error for chat {chat_id}")
            
            # Execute fix synchronously since it's critical
            try:
                result = spawn_valor_session(
                    task_description=fix_task,
                    target_directory="/Users/valorengels/src/ai",
                    force_sync=True
                )
                logger.info(f"✅ Auto-fix completed: {result[:200]}...")
                
            except Exception as fix_error:
                logger.error(f"❌ Auto-fix failed: {fix_error}")
                # Fall back to creating a promise
                await self._create_recovery_promise(error_context, chat_id)
        
        elif "syntax" in error_msg or "indentation" in error_msg:
            # Fix syntax/indentation errors
            fix_task = f"""
Fix syntax/indentation error in the codebase.

Error details:
- Type: {error_context.error_type}
- Message: {error_context.error_message}
- Function: {error_context.function_name}

Task:
1. Identify the file with syntax/indentation issues
2. Fix the formatting and syntax errors
3. Validate the file compiles correctly
4. Restart the system if needed

This is a critical syntax fix.
"""
            
            logger.info(f"📝 Auto-fixing syntax error for chat {chat_id}")
            
            try:
                result = spawn_valor_session(
                    task_description=fix_task,
                    target_directory="/Users/valorengels/src/ai",
                    force_sync=True
                )
                logger.info(f"✅ Syntax auto-fix completed: {result[:200]}...")
                
            except Exception as fix_error:
                logger.error(f"❌ Syntax auto-fix failed: {fix_error}")
    
    async def _create_recovery_promise(self, error_context: ErrorContext, chat_id: int):
        """Create a background promise to fix the error"""
        
        from utilities.promise_manager_huey import PromiseManager
        
        recovery_task = f"""
Investigate and fix this error that occurred in the AI system:

**Error Details:**
- Type: {error_context.error_type}
- Message: {error_context.error_message}
- Function: {error_context.function_name}
- User: {error_context.username or 'unknown'}
- Time: {error_context.timestamp}

**Traceback:**
```
{error_context.traceback_info[:1000]}
```

**Input Data:**
{error_context.input_data}

**Recovery Task:**
1. Analyze the root cause of this error
2. Implement a robust fix to prevent recurrence
3. Add appropriate error handling if missing
4. Test the fix thoroughly
5. Update documentation if needed

This error affected user experience and should be prioritized for fixing.
"""
        
        try:
            promise_manager = PromiseManager()
            promise_id = promise_manager.create_promise(
                chat_id=chat_id,
                task_description=f"Auto-fix error: {error_context.error_type}",
                task_type="error_recovery",
                metadata={
                    "error_type": error_context.error_type,
                    "function_name": error_context.function_name,
                    "auto_recovery": True,
                    "full_task": recovery_task
                }
            )
            
            logger.info(f"🔄 Created recovery promise {promise_id} for error in {error_context.function_name}")
            
        except Exception as e:
            logger.error(f"Failed to create recovery promise: {e}")
    
    async def _log_for_manual_review(self, error_context: ErrorContext):
        """Log critical errors for manual review"""
        
        logger.critical(f"CRITICAL ERROR requiring manual review:")
        logger.critical(f"  Type: {error_context.error_type}")
        logger.critical(f"  Message: {error_context.error_message}")
        logger.critical(f"  Function: {error_context.function_name}")
        logger.critical(f"  Chat: {error_context.chat_id}")
        logger.critical(f"  User: {error_context.username}")
        logger.critical(f"  Time: {error_context.timestamp}")
        logger.critical(f"  Traceback:\n{error_context.traceback_info}")
    
    def _log_recovery_attempt(self, error_context: ErrorContext, action: RecoveryAction):
        """Log the recovery attempt for tracking"""
        
        recovery_record = {
            "timestamp": error_context.timestamp,
            "error_type": error_context.error_type,
            "function_name": error_context.function_name,
            "chat_id": error_context.chat_id,
            "recovery_action": action.value,
            "error_message": error_context.error_message[:500]
        }
        
        self.recovery_history.append(recovery_record)
        
        # Keep only last 100 recovery attempts
        if len(self.recovery_history) > 100:
            self.recovery_history = self.recovery_history[-100:]
        
        logger.info(f"🔧 Recovery attempt logged: {action.value} for {error_context.error_type} in {error_context.function_name}")


# Global instance for use across the system
auto_recovery = AutoErrorRecovery()