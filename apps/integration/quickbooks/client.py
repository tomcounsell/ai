"""
QuickBooks API client.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from django.conf import settings

logger = logging.getLogger(__name__)


class QuickBooksClient:
    """Client for QuickBooks Online API."""

    BASE_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company"
    PROD_URL = "https://quickbooks.api.intuit.com/v3/company"

    def __init__(self, organization_id: str):
        self.organization_id = organization_id
        self._session = None
        self._access_token = None
        self._company_id = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if not self._session:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_credentials(self) -> tuple[str, str]:
        """Get QuickBooks credentials from database."""
        from apps.integration.models import QuickBooksConnection

        try:
            connection = await QuickBooksConnection.objects.aget(
                organization_id=self.organization_id,
                is_active=True,
            )
            return connection.access_token, connection.company_id
        except QuickBooksConnection.DoesNotExist:
            logger.error(
                f"No active QuickBooks connection for org {self.organization_id}"
            )
            raise

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json_data: dict | None = None,
    ) -> dict[str, Any]:
        """Make authenticated request to QuickBooks API."""

        if not self._access_token or not self._company_id:
            self._access_token, self._company_id = await self._get_credentials()

        session = await self._get_session()

        base_url = self.PROD_URL if settings.PRODUCTION else self.BASE_URL
        url = f"{base_url}/{self._company_id}/{endpoint}"

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        async with session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def create_invoice(
        self,
        customer_id: str,
        line_items: list[dict[str, Any]],
        due_date: str | None = None,
        invoice_number: str | None = None,
    ) -> dict[str, Any]:
        """Create a new invoice in QuickBooks."""

        invoice_data = {
            "Line": line_items,
            "CustomerRef": {"value": customer_id},
        }

        if due_date:
            invoice_data["DueDate"] = due_date

        if invoice_number:
            invoice_data["DocNumber"] = invoice_number

        result = await self._make_request("POST", "invoice", json_data=invoice_data)
        return result.get("Invoice", {})

    async def search_customers(
        self,
        query: str,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search for customers in QuickBooks."""

        sql_query = f"SELECT * FROM Customer WHERE DisplayName LIKE '%{query}%' MAXRESULTS {max_results}"

        result = await self._make_request(
            "GET",
            "query",
            params={"query": sql_query},
        )

        return result.get("QueryResponse", {}).get("Customer", [])

    async def get_customer(self, customer_id: str) -> dict[str, Any]:
        """Get customer details by ID."""

        result = await self._make_request("GET", f"customer/{customer_id}")
        return result.get("Customer", {})

    async def list_customers(self) -> list[dict[str, Any]]:
        """List all customers."""

        result = await self._make_request(
            "GET",
            "query",
            params={"query": "SELECT * FROM Customer MAXRESULTS 100"},
        )

        return result.get("QueryResponse", {}).get("Customer", [])

    async def list_invoices(
        self,
        customer_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List invoices with optional filters."""

        query_parts = ["SELECT * FROM Invoice"]
        where_clauses = []

        if customer_id:
            where_clauses.append(f"CustomerRef = '{customer_id}'")

        if status:
            # Map status to QuickBooks balance condition
            if status == "paid":
                where_clauses.append("Balance = '0'")
            elif status == "unpaid":
                where_clauses.append("Balance > '0'")

        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))

        query_parts.append(f"MAXRESULTS {limit}")
        sql_query = " ".join(query_parts)

        result = await self._make_request(
            "GET",
            "query",
            params={"query": sql_query},
        )

        return result.get("QueryResponse", {}).get("Invoice", [])

    async def list_items(self) -> list[dict[str, Any]]:
        """List all items/products."""

        result = await self._make_request(
            "GET",
            "query",
            params={"query": "SELECT * FROM Item MAXRESULTS 100"},
        )

        return result.get("QueryResponse", {}).get("Item", [])

    async def generate_report(
        self,
        report_type: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Generate a financial report."""

        # Map report types to QuickBooks endpoints
        report_endpoints = {
            "ProfitAndLoss": "ProfitAndLoss",
            "BalanceSheet": "BalanceSheet",
            "CashFlow": "CashFlow",
            "CustomerSales": "CustomerSales",
            "ItemSales": "ItemSales",
        }

        endpoint = report_endpoints.get(report_type)
        if not endpoint:
            raise ValueError(f"Unknown report type: {report_type}")

        result = await self._make_request(
            "GET",
            f"reports/{endpoint}",
            params={
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        return result

    async def list_available_reports(self) -> list[dict[str, str]]:
        """List available report types."""

        return [
            {"type": "ProfitAndLoss", "name": "Profit and Loss"},
            {"type": "BalanceSheet", "name": "Balance Sheet"},
            {"type": "CashFlow", "name": "Cash Flow Statement"},
            {"type": "CustomerSales", "name": "Sales by Customer"},
            {"type": "ItemSales", "name": "Sales by Item"},
        ]

    async def sync_entity(self, entity_type: str) -> int:
        """Sync a specific entity type from QuickBooks."""

        # Map entity types to QuickBooks entities
        entity_map = {
            "customers": "Customer",
            "invoices": "Invoice",
            "items": "Item",
            "vendors": "Vendor",
            "bills": "Bill",
            "payments": "Payment",
        }

        qb_entity = entity_map.get(entity_type)
        if not qb_entity:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Fetch all entities of this type
        result = await self._make_request(
            "GET",
            "query",
            params={"query": f"SELECT * FROM {qb_entity} MAXRESULTS 1000"},
        )

        entities = result.get("QueryResponse", {}).get(qb_entity, [])

        # In production, save to database
        # For now, just return count
        return len(entities)

    async def close(self):
        """Close the client session."""
        if self._session:
            await self._session.close()
