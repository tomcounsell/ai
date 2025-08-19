#!/usr/bin/env python3
"""
Simple test server to verify the AI Rebuild system is working
"""

import asyncio
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_imports():
    """Test that all major components can be imported"""
    results = []
    
    try:
        from config import settings
        results.append("✅ Config loaded")
        logger.info(f"Environment: {settings.environment}")
    except Exception as e:
        results.append(f"❌ Config failed: {e}")
    
    try:
        from utilities.database import DatabaseManager
        db_manager = DatabaseManager()
        await db_manager.initialize()
        results.append("✅ Database initialized")
    except Exception as e:
        results.append(f"❌ Database failed: {e}")
    
    try:
        from agents import ValorAgent, ValorContext
        results.append("✅ Agents loaded")
    except Exception as e:
        results.append(f"❌ Agents failed: {e}")
    
    try:
        from tools import ToolImplementation
        results.append("✅ Tools loaded")
    except Exception as e:
        results.append(f"❌ Tools failed: {e}")
    
    try:
        from mcp_servers import MCPServer
        results.append("✅ MCP Servers loaded")
    except Exception as e:
        results.append(f"❌ MCP Servers failed: {e}")
    
    return results

async def test_agent_creation():
    """Test creating an agent instance"""
    try:
        from agents.valor.context import ValorContext
        from agents.valor.agent import ValorAgent
        
        # Create a context
        context = ValorContext(
            chat_id="test_chat",
            user_name="Test User",
            workspace="test_workspace"
        )
        
        # Create an agent (without API key, just for structure test)
        agent = ValorAgent()
        
        return "✅ Agent created successfully"
    except Exception as e:
        return f"❌ Agent creation failed: {e}"

async def test_tool_discovery():
    """Test tool discovery system"""
    try:
        from agents.tool_registry import discover_tools
        
        tools = discover_tools()
        tool_count = len(tools)
        
        if tool_count > 0:
            return f"✅ Discovered {tool_count} tools"
        else:
            return "⚠️  No tools discovered"
    except Exception as e:
        return f"❌ Tool discovery failed: {e}"

async def test_database_operations():
    """Test basic database operations"""
    try:
        from utilities.database import DatabaseManager
        
        db = DatabaseManager()
        await db.initialize()
        
        # Try to create a test project
        project_id = await db.create_project(
            name="Test Project",
            workspace_path="/test/path",
            metadata={"test": True}
        )
        
        if project_id:
            # Clean up
            await db.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            return f"✅ Database operations working (created project {project_id})"
        else:
            return "❌ Database operations failed"
    except Exception as e:
        return f"❌ Database operations failed: {e}"

async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("AI REBUILD SYSTEM TEST")
    print("="*60 + "\n")
    
    # Test imports
    print("1. Testing Component Imports...")
    import_results = await test_imports()
    for result in import_results:
        print(f"   {result}")
    
    print("\n2. Testing Agent Creation...")
    agent_result = await test_agent_creation()
    print(f"   {agent_result}")
    
    print("\n3. Testing Tool Discovery...")
    tool_result = await test_tool_discovery()
    print(f"   {tool_result}")
    
    print("\n4. Testing Database Operations...")
    db_result = await test_database_operations()
    print(f"   {db_result}")
    
    # Summary
    print("\n" + "="*60)
    all_results = import_results + [agent_result, tool_result, db_result]
    success_count = sum(1 for r in all_results if "✅" in r)
    total_count = len(all_results)
    
    if success_count == total_count:
        print(f"✅ ALL TESTS PASSED ({success_count}/{total_count})")
        print("\nThe AI Rebuild system is ready to use!")
    else:
        print(f"⚠️  SOME TESTS FAILED ({success_count}/{total_count} passed)")
        print("\nPlease check the failed components above.")
    
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())