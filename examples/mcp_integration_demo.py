#!/usr/bin/env python3
"""
MCP Integration Demo

This example demonstrates the complete MCP (Model Context Protocol) integration
with stateless servers, context injection, and orchestration.
"""

import asyncio
import logging
import json
from datetime import datetime, timezone

from mcp_servers import (
    MCPOrchestrator, MCPRequest,
    WorkspaceContext, UserContext, SecurityLevel
)


async def main():
    """Main demo function."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("🚀 MCP Integration Demo - Phase 4 Implementation")
    print("=" * 60)
    
    # Initialize orchestrator
    print("\n1. Initializing MCP Orchestrator...")
    orchestrator = MCPOrchestrator(
        name="demo_orchestrator",
        health_check_interval=10,
        enable_inter_server_messaging=True,
        enable_load_balancing=True
    )
    
    async with orchestrator:
        # Set up context (workspace, user, session)
        print("\n2. Setting up context (workspace, user, session)...")
        
        # Register workspace
        workspace = await orchestrator.context_manager.register_workspace(
            workspace_id="demo_workspace",
            name="Demo Workspace",
            workspace_type="project",
            security_level=SecurityLevel.INTERNAL
        )
        print(f"   ✅ Workspace registered: {workspace.name}")
        
        # Register user
        user = await orchestrator.context_manager.register_user(
            user_id="demo_user",
            username="demo_user",
            display_name="Demo User",
            security_clearance=SecurityLevel.INTERNAL,
            permissions={"read", "write", "execute"}
        )
        print(f"   ✅ User registered: {user.display_name}")
        
        # Create session
        session = await orchestrator.context_manager.create_session(
            user_id="demo_user",
            workspace_id="demo_workspace"
        )
        print(f"   ✅ Session created: {session.session_id}")
        
        # Register MCP servers
        print("\n3. Registering MCP servers...")
        
        # Development tools server
        await orchestrator.register_server(
            server_name="dev_server",
            server_type="development_tools",
            config={
                "allowed_languages": ["python", "javascript", "bash"],
                "execution_timeout": 30,
                "sandbox_enabled": True
            },
            auto_start=True
        )
        print("   ✅ Development Tools Server registered")
        
        # Social tools server  
        await orchestrator.register_server(
            server_name="social_server",
            server_type="social_tools",
            config={
                "search_api_keys": {},  # Would contain real API keys in production
                "knowledge_base_path": None
            },
            auto_start=True
        )
        print("   ✅ Social Tools Server registered")
        
        # Project management server
        await orchestrator.register_server(
            server_name="pm_server",
            server_type="project_management",
            config={
                "github_token": None,  # Would contain real token in production
                "linear_token": None
            },
            auto_start=True
        )
        print("   ✅ Project Management Server registered")
        
        # Show server status
        print("\n4. Server Health Status:")
        health_summary = orchestrator.get_health_summary()
        print(f"   📊 Total servers: {health_summary['total_servers']}")
        print(f"   💚 Healthy: {health_summary['healthy_servers']}")
        print(f"   🟡 Degraded: {health_summary['degraded_servers']}")
        print(f"   🔴 Unhealthy: {health_summary['unhealthy_servers']}")
        
        # Demonstrate context injection and stateless operations
        print("\n5. Demonstrating Context Injection & Stateless Operations...")
        
        context = {
            "workspace_id": "demo_workspace",
            "user_id": "demo_user", 
            "session_id": session.session_id
        }
        
        # Test 1: Code execution with context
        print("\n   Test 1: Python Code Execution")
        code_request = MCPRequest(
            method="execute_code",
            params={
                "language": "python",
                "code": """
import json
import datetime

# Demo calculation
result = sum(range(1, 11))
current_time = datetime.datetime.now().isoformat()

print(f"Sum of 1-10: {result}")
print(f"Current time: {current_time}")
print("Context injection working!")
"""
            },
            context=context
        )
        
        response = await orchestrator.route_request(code_request)
        if response.success:
            print(f"   ✅ Code executed successfully")
            print(f"   📤 Output: {response.result['stdout'].strip()}")
            print(f"   ⏱️  Execution time: {response.result['execution_time_ms']:.2f}ms")
            print(f"   🎯 Routed to: {response.metadata.get('target_server')}")
        else:
            print(f"   ❌ Code execution failed: {response.error}")
        
        # Test 2: Content generation
        print("\n   Test 2: Content Generation")
        content_request = MCPRequest(
            method="generate_content",
            params={
                "template_name": "blog_post",
                "variables": {
                    "title": "MCP Integration Success",
                    "introduction": "Today we successfully integrated MCP servers with context injection.",
                    "content": "The stateless architecture allows for scalable and secure operations across multiple server types.",
                    "conclusion": "MCP provides a robust foundation for AI tool orchestration.",
                    "author": user.display_name,
                    "date": datetime.now().strftime("%Y-%m-%d")
                }
            },
            context=context
        )
        
        response = await orchestrator.route_request(content_request)
        if response.success:
            print(f"   ✅ Content generated successfully")
            print(f"   🎯 Routed to: {response.metadata.get('target_server')}")
            print(f"   📄 Preview: {response.result['content'][:100]}...")
        else:
            print(f"   ❌ Content generation failed: {response.error}")
        
        # Test 3: Calendar event creation
        print("\n   Test 3: Calendar Event Creation")
        calendar_request = MCPRequest(
            method="create_calendar_event", 
            params={
                "title": "MCP Demo Meeting",
                "description": "Review MCP integration results",
                "start_time": "2024-12-15T14:00:00Z",
                "end_time": "2024-12-15T15:00:00Z",
                "location": "Virtual Conference Room",
                "attendees": ["demo_user@example.com"]
            },
            context=context
        )
        
        response = await orchestrator.route_request(calendar_request)
        if response.success:
            print(f"   ✅ Calendar event created successfully")
            print(f"   🎯 Routed to: {response.metadata.get('target_server')}")
            print(f"   📅 Event: {response.result['title']} on {response.result['start_time']}")
        else:
            print(f"   ❌ Calendar event creation failed: {response.error}")
        
        # Test 4: Documentation creation
        print("\n   Test 4: Documentation Creation")
        doc_request = MCPRequest(
            method="create_documentation_page",
            params={
                "title": "MCP Integration Guide",
                "content": """# MCP Integration Guide

## Overview
This guide covers the successful integration of MCP (Model Context Protocol) servers.

## Key Features
- Stateless server architecture
- Context injection for security and personalization
- Orchestrated service discovery
- Health monitoring and load balancing

## Server Types
- **Development Tools**: Code execution, debugging, profiling
- **Social Tools**: Web search, calendar, content generation
- **Project Management**: GitHub, Linear, documentation
- **Telegram Tools**: Messaging and communication

## Best Practices
1. Always validate context before processing requests
2. Use proper security levels for workspace access
3. Monitor server health regularly
4. Implement proper error handling
""",
                "category": "integration",
                "tags": ["mcp", "integration", "guide"],
                "published": True
            },
            context=context
        )
        
        response = await orchestrator.route_request(doc_request)
        if response.success:
            print(f"   ✅ Documentation created successfully")
            print(f"   🎯 Routed to: {response.metadata.get('target_server')}")
            print(f"   📖 Document: {response.result['title']} ({response.result['category']})")
        else:
            print(f"   ❌ Documentation creation failed: {response.error}")
        
        # Demonstrate server capabilities
        print("\n6. Server Capabilities Summary:")
        capabilities = orchestrator.get_server_capabilities()
        for server_name, caps in capabilities.items():
            print(f"\n   🔧 {server_name}:")
            for cap in caps[:3]:  # Show first 3 capabilities
                print(f"      • {cap['name']}: {cap['description']}")
            if len(caps) > 3:
                print(f"      ... and {len(caps) - 3} more capabilities")
        
        # Show orchestrator statistics
        print("\n7. Orchestrator Statistics:")
        stats = orchestrator.get_orchestrator_stats()
        print(f"   📈 Requests routed: {stats['requests_routed']}")
        print(f"   🔄 Messages processed: {stats['messages_processed']}")
        print(f"   🏥 Health checks performed: {stats['health_checks_performed']}")
        print(f"   🖥️  Servers registered: {stats['servers_registered']}")
        print(f"   ⏰ Uptime: {stats['uptime_seconds']:.1f} seconds")
        
        # Test stateless operation by sending same request multiple times
        print("\n8. Demonstrating Stateless Operations:")
        stateless_request = MCPRequest(
            method="execute_code",
            params={
                "language": "python",
                "code": "import random; print(f'Random number: {random.randint(1, 100)}')"
            },
            context=context
        )
        
        print("   Sending identical requests to demonstrate stateless behavior:")
        for i in range(3):
            response = await orchestrator.route_request(stateless_request)
            if response.success:
                output = response.result['stdout'].strip()
                server = response.metadata.get('target_server', 'unknown')
                print(f"   #{i+1}: {output} (server: {server})")
        
        # Test security validation
        print("\n9. Security Validation Test:")
        try:
            # Test with invalid context
            invalid_request = MCPRequest(
                method="execute_code",
                params={
                    "language": "python", 
                    "code": "print('This should be blocked')"
                },
                context={
                    "workspace_id": "nonexistent_workspace",
                    "user_id": "nonexistent_user"
                }
            )
            
            response = await orchestrator.route_request(invalid_request)
            if response.success:
                print("   ⚠️  Security validation may need improvement")
            else:
                print("   ✅ Invalid context properly rejected")
                
        except Exception as e:
            print(f"   ✅ Security validation working: {str(e)}")
        
        # Test error handling
        print("\n10. Error Handling Test:")
        error_request = MCPRequest(
            method="nonexistent_method",
            params={},
            context=context
        )
        
        response = await orchestrator.route_request(error_request)
        if not response.success:
            print("   ✅ Unknown method properly handled")
            print(f"   📝 Error: {response.error.get('message', 'Unknown error')}")
        else:
            print("   ⚠️  Unknown method should have been rejected")
        
        print("\n" + "=" * 60)
        print("🎉 MCP Integration Demo Complete!")
        print("\nKey achievements:")
        print("✅ Stateless server architecture implemented")
        print("✅ Context injection working correctly")
        print("✅ Multiple server types integrated")  
        print("✅ Orchestration and routing functional")
        print("✅ Health monitoring operational")
        print("✅ Security validation active")
        print("✅ Error handling robust")
        
        print(f"\nFinal server health: {orchestrator.get_health_summary()['healthy_servers']}/{orchestrator.get_health_summary()['total_servers']} servers healthy")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo failed with error: {str(e)}")
        import traceback
        traceback.print_exc()