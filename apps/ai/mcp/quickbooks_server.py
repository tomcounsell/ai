"""
QuickBooks MCP Server implementation.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import Resource, Tool
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .quickbooks_tools import QUICKBOOKS_TOOLS
from apps.integration.quickbooks.client import QuickBooksClient

logger = logging.getLogger(__name__)


class QuickBooksMCPServer:
    """MCP Server for QuickBooks integration."""
    
    def __init__(self, organization_id: str, api_key: str):
        self.organization_id = organization_id
        self.api_key = api_key
        self.server = Server("quickbooks-mcp")
        self.qb_client = QuickBooksClient(organization_id)
        
        # Register handlers
        self._register_handlers()
        
    def _register_handlers(self):
        """Register MCP protocol handlers."""
        
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available QuickBooks tools."""
            return QUICKBOOKS_TOOLS
            
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
            """Execute a QuickBooks tool."""
            
            if name == "quickbooks_create_invoice":
                return await self._create_invoice(arguments)
            elif name == "quickbooks_search_customers":
                return await self._search_customers(arguments)
            elif name == "quickbooks_get_customer":
                return await self._get_customer(arguments)
            elif name == "quickbooks_list_invoices":
                return await self._list_invoices(arguments)
            elif name == "quickbooks_generate_report":
                return await self._generate_report(arguments)
            elif name == "quickbooks_sync_data":
                return await self._sync_data(arguments)
            else:
                raise ValueError(f"Unknown tool: {name}")
                
        @self.server.list_resources()
        async def list_resources() -> List[Resource]:
            """List available QuickBooks resources."""
            return [
                Resource(
                    uri="quickbooks://customers",
                    name="QuickBooks Customers",
                    description="Access to QuickBooks customer data",
                    mimeType="application/json",
                ),
                Resource(
                    uri="quickbooks://invoices",
                    name="QuickBooks Invoices",
                    description="Access to QuickBooks invoice data",
                    mimeType="application/json",
                ),
                Resource(
                    uri="quickbooks://items",
                    name="QuickBooks Items",
                    description="Access to QuickBooks item/product data",
                    mimeType="application/json",
                ),
                Resource(
                    uri="quickbooks://reports",
                    name="QuickBooks Reports",
                    description="Access to QuickBooks financial reports",
                    mimeType="application/json",
                ),
            ]
            
        @self.server.read_resource()
        async def read_resource(uri: str) -> str:
            """Read a QuickBooks resource."""
            
            if uri == "quickbooks://customers":
                data = await self.qb_client.list_customers()
            elif uri == "quickbooks://invoices":
                data = await self.qb_client.list_invoices()
            elif uri == "quickbooks://items":
                data = await self.qb_client.list_items()
            elif uri == "quickbooks://reports":
                data = await self.qb_client.list_available_reports()
            else:
                raise ValueError(f"Unknown resource: {uri}")
                
            return json.dumps(data, indent=2)
            
    async def _create_invoice(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new QuickBooks invoice."""
        return await self.qb_client.create_invoice(
            customer_id=params["customer_id"],
            line_items=params["line_items"],
            due_date=params.get("due_date"),
            invoice_number=params.get("invoice_number"),
        )
        
    async def _search_customers(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Search QuickBooks customers."""
        return await self.qb_client.search_customers(
            query=params["query"],
            max_results=params.get("max_results", 20),
        )
        
    async def _get_customer(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get QuickBooks customer by ID."""
        return await self.qb_client.get_customer(params["customer_id"])
        
    async def _list_invoices(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List QuickBooks invoices."""
        return await self.qb_client.list_invoices(
            customer_id=params.get("customer_id"),
            status=params.get("status"),
            limit=params.get("limit", 20),
        )
        
    async def _generate_report(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate QuickBooks report."""
        return await self.qb_client.generate_report(
            report_type=params["report_type"],
            start_date=params["start_date"],
            end_date=params["end_date"],
        )
        
    async def _sync_data(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sync QuickBooks data."""
        results = {}
        for entity_type in params["entity_types"]:
            count = await self.qb_client.sync_entity(entity_type)
            results[entity_type] = count
        return {
            "status": "success",
            "synced": results,
        }
        
    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


async def main():
    """Main entry point for the MCP server."""
    import os
    
    organization_id = os.environ.get("QUICKBOOKS_ORG_ID")
    api_key = os.environ.get("QUICKBOOKS_API_KEY")
    
    if not organization_id or not api_key:
        logger.error("Missing required environment variables")
        return
        
    server = QuickBooksMCPServer(organization_id, api_key)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())