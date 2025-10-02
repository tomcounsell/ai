"""
QuickBooks MCP Server implementation using FastMCP.
"""

import logging
import os
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from apps.integration.quickbooks.client import QuickBooksClient

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("QuickBooks MCP")

# QuickBooks client instance (will be initialized with org_id)
_qb_client: QuickBooksClient | None = None


def get_qb_client() -> QuickBooksClient:
    """Get QuickBooks client instance."""
    if _qb_client is None:
        raise RuntimeError("QuickBooks client not initialized")
    return _qb_client


def initialize_client(organization_id: str):
    """Initialize QuickBooks client."""
    global _qb_client
    _qb_client = QuickBooksClient(organization_id)


# Resources
@mcp.resource("quickbooks://customers")
async def get_customers() -> dict:
    """Access QuickBooks customer data."""
    client = get_qb_client()
    customers = await client.list_customers()
    return {"customers": customers}


@mcp.resource("quickbooks://invoices")
async def get_invoices() -> dict:
    """Access QuickBooks invoice data."""
    client = get_qb_client()
    invoices = await client.list_invoices()
    return {"invoices": invoices}


@mcp.resource("quickbooks://items")
async def get_items() -> dict:
    """Access QuickBooks item/product data."""
    client = get_qb_client()
    items = await client.list_items()
    return {"items": items}


@mcp.resource("quickbooks://reports")
async def get_reports() -> dict:
    """Access QuickBooks available reports."""
    client = get_qb_client()
    reports = await client.list_available_reports()
    return {"reports": reports}


# Tools
@mcp.tool()
async def create_invoice(
    customer_id: str,
    line_items: List[Dict[str, Any]],
    due_date: str | None = None,
    invoice_number: str | None = None,
) -> dict:
    """
    Create a new QuickBooks invoice.

    Args:
        customer_id: ID of the customer for the invoice
        line_items: List of line items with item_id, quantity, rate
        due_date: Optional due date in YYYY-MM-DD format
        invoice_number: Optional custom invoice number
    """
    client = get_qb_client()
    return await client.create_invoice(
        customer_id=customer_id,
        line_items=line_items,
        due_date=due_date,
        invoice_number=invoice_number,
    )


@mcp.tool()
async def search_customers(
    query: str,
    max_results: int = 20,
) -> List[dict]:
    """
    Search QuickBooks customers.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (default: 20)
    """
    client = get_qb_client()
    return await client.search_customers(query=query, max_results=max_results)


@mcp.tool()
async def get_customer(customer_id: str) -> dict:
    """
    Get QuickBooks customer by ID.

    Args:
        customer_id: ID of the customer to retrieve
    """
    client = get_qb_client()
    return await client.get_customer(customer_id)


@mcp.tool()
async def list_invoices(
    customer_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> List[dict]:
    """
    List QuickBooks invoices.

    Args:
        customer_id: Optional filter by customer ID
        status: Optional filter by status (e.g., 'paid', 'open')
        limit: Maximum number of invoices to return (default: 20)
    """
    client = get_qb_client()
    return await client.list_invoices(
        customer_id=customer_id,
        status=status,
        limit=limit,
    )


@mcp.tool()
async def generate_report(
    report_type: str,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Generate QuickBooks financial report.

    Args:
        report_type: Type of report (e.g., 'ProfitAndLoss', 'BalanceSheet')
        start_date: Report start date in YYYY-MM-DD format
        end_date: Report end date in YYYY-MM-DD format
    """
    client = get_qb_client()
    return await client.generate_report(
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
    )


@mcp.tool()
async def sync_data(entity_types: List[str]) -> dict:
    """
    Sync QuickBooks data for specified entity types.

    Args:
        entity_types: List of entity types to sync (e.g., ['customers', 'invoices'])
    """
    client = get_qb_client()
    results = {}
    for entity_type in entity_types:
        count = await client.sync_entity(entity_type)
        results[entity_type] = count
    return {
        "status": "success",
        "synced": results,
    }


def main():
    """Main entry point for the MCP server."""
    organization_id = os.environ.get("QUICKBOOKS_ORG_ID")
    api_key = os.environ.get("QUICKBOOKS_API_KEY")

    if not organization_id:
        logger.error("Missing QUICKBOOKS_ORG_ID environment variable")
        return

    if not api_key:
        logger.error("Missing QUICKBOOKS_API_KEY environment variable")
        return

    # Initialize QuickBooks client
    initialize_client(organization_id)

    # Run the MCP server (starts event loop internally)
    mcp.run()


if __name__ == "__main__":
    main()
