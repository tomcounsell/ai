"""
MCP tools for QuickBooks integration.
"""

from typing import Any, Dict, List, Optional

from mcp import Tool
from pydantic import BaseModel, Field


class InvoiceCreateParams(BaseModel):
    """Parameters for creating a QuickBooks invoice."""
    
    customer_id: str = Field(description="QuickBooks customer ID")
    line_items: List[Dict[str, Any]] = Field(description="Invoice line items")
    due_date: Optional[str] = Field(description="Due date (YYYY-MM-DD)")
    invoice_number: Optional[str] = Field(description="Custom invoice number")


class CustomerSearchParams(BaseModel):
    """Parameters for searching QuickBooks customers."""
    
    query: str = Field(description="Search query (name, email, etc.)")
    max_results: int = Field(default=20, description="Maximum results to return")


class ReportParams(BaseModel):
    """Parameters for generating QuickBooks reports."""
    
    report_type: str = Field(
        description="Report type (ProfitAndLoss, BalanceSheet, etc.)"
    )
    start_date: str = Field(description="Start date (YYYY-MM-DD)")
    end_date: str = Field(description="End date (YYYY-MM-DD)")


# MCP Tool definitions
QUICKBOOKS_TOOLS = [
    Tool(
        name="quickbooks_create_invoice",
        description="Create a new invoice in QuickBooks",
        input_schema=InvoiceCreateParams.model_json_schema(),
    ),
    Tool(
        name="quickbooks_search_customers",
        description="Search for customers in QuickBooks",
        input_schema=CustomerSearchParams.model_json_schema(),
    ),
    Tool(
        name="quickbooks_get_customer",
        description="Get customer details by ID",
        input_schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "QuickBooks customer ID"},
            },
            "required": ["customer_id"],
        },
    ),
    Tool(
        name="quickbooks_list_invoices",
        description="List invoices with optional filters",
        input_schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Filter by customer ID"},
                "status": {"type": "string", "description": "Invoice status (paid, unpaid, etc.)"},
                "limit": {"type": "integer", "description": "Maximum results", "default": 20},
            },
        },
    ),
    Tool(
        name="quickbooks_generate_report",
        description="Generate financial reports from QuickBooks",
        input_schema=ReportParams.model_json_schema(),
    ),
    Tool(
        name="quickbooks_sync_data",
        description="Sync QuickBooks data to local cache",
        input_schema={
            "type": "object",
            "properties": {
                "entity_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Entity types to sync (customers, invoices, items, etc.)",
                },
            },
            "required": ["entity_types"],
        },
    ),
]