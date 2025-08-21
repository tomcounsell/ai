# QuickBooks API Complete Developer Resources & Documentation

## Table of Contents
1. [Core Documentation](#core-documentation)
2. [Authentication & OAuth 2.0](#authentication--oauth-20)
3. [API Reference & Endpoints](#api-reference--endpoints)
4. [SDKs & Libraries](#sdks--libraries)
5. [Rate Limits & Best Practices](#rate-limits--best-practices)
6. [Webhooks](#webhooks)
7. [Error Handling](#error-handling)
8. [Developer Tools & Utilities](#developer-tools--utilities)
9. [Community Resources & Tutorials](#community-resources--tutorials)
10. [API Limitations & Workarounds](#api-limitations--workarounds)

---

## Core Documentation

### Official QuickBooks Developer Portal
- **Main Developer Portal**: https://developer.intuit.com
- **Getting Started Guide**: https://developer.intuit.com/app/developer/qbo/docs/get-started
- **API Development Guide**: https://developer.intuit.com/app/developer/qbo/docs/develop
- **QuickBooks Online API Documentation**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account

### Environment URLs
```
Production API Base: https://quickbooks.api.intuit.com
Sandbox API Base: https://sandbox-quickbooks.api.intuit.com
OAuth Base (Both): https://oauth.platform.intuit.com
```

*Source: [QuickBooks API Integration Guide](https://zuplo.com/blog/2025/05/20/quickbooks-api)*

---

## Authentication & OAuth 2.0

### OAuth 2.0 Documentation
- **OAuth 2.0 Setup Guide**: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0
- **Authentication & Authorization**: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization
- **OAuth FAQ**: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/faq
- **OAuth 2.0 Playground**: https://developer.intuit.com/app/developer/playground

### Key OAuth Endpoints
```
Authorization: https://appcenter.intuit.com/connect/oauth2
Token Exchange: https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
Token Revoke: https://developer.api.intuit.com/v2/oauth2/tokens/revoke
```

### Token Specifications
- **Access Token**: Expires in 1 hour (3600 seconds)
- **Refresh Token**: Expires in 100 days
- **Required Scopes**: `com.intuit.quickbooks.accounting` for accounting API

*Sources: [QuickBooks OAuth Guide](https://stateful.com/blog/quickbooks-oauth), [QuickBooks API Integration In-Depth](https://www.getknit.dev/blog/quickbooks-online-api-integration-guide-in-depth)*

---

## API Reference & Endpoints

### Main Entity Endpoints
- **Account API**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/account
- **Invoice API**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/invoice
- **Customer API**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/customer
- **Payment API**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/payment
- **Vendor API**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/vendor

### API Features Documentation
- **REST API Features**: https://developer.intuit.com/app/developer/qbo/docs/learn/rest-api-features
- **Query Operations**: https://developer.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api
- **Batch Operations**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/batch

### Key API Capabilities
- Query data using SQL-like syntax
- Maximum 1000 entities per response
- Default 100 entities if not specified
- Support for pagination with STARTPOSITION and MAXRESULTS
- Batch operations for bulk create/update/delete

*Sources: [QuickBooks API Reference](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/account), [QuickBooks Online API Integration](https://www.merge.dev/blog/quickbooks-api)*

---

## SDKs & Libraries

### Official & Community SDKs

#### Node.js/JavaScript
- **node-quickbooks** (Community): 
  - NPM: https://www.npmjs.com/package/node-quickbooks
  - GitHub: https://github.com/mcohen01/node-quickbooks
  - Install: `npm install node-quickbooks`
  
- **quickbooks-api** (TypeScript):
  - NPM: https://www.npmjs.com/package/quickbooks-api
  - Features: Full TypeScript support, automatic token refresh
  - Install: `npm install quickbooks-api`

#### Python
- **python-quickbooks**:
  - PyPI: https://pypi.org/project/python-quickbooks/
  - GitHub: https://github.com/ej2/python-quickbooks
  - Install: `pip install python-quickbooks`
  - Requires: `intuit-oauth` for OAuth 2.0 support

#### Other Languages
- **PHP SDK**: Available through Intuit Developer Portal
- **.NET SDK**: Available through Intuit Developer Portal
- **Java SDK**: Available through Intuit Developer Portal

### Authentication Libraries
- **intuit-oauth (Node.js)**: OAuth 2.0 client for Node.js
- **intuitlib (Python)**: OAuth 2.0 client for Python

*Sources: [NPM node-quickbooks](https://www.npmjs.com/package/node-quickbooks), [PyPI python-quickbooks](https://pypi.org/project/python-quickbooks/), [GitHub python-quickbooks](https://github.com/ej2/python-quickbooks)*

---

## Rate Limits & Best Practices

### API Rate Limits
- **QuickBooks Online API**: 
  - 500 requests per minute per realm ID
  - 500 requests per minute across all realms (combined)
  - Returns 429 "Too Many Requests" when exceeded

- **QuickBooks Time API** (formerly TSheets):
  - 300 calls within any 5-minute window
  - Per access token basis

### Best Practices Documentation
- **API Optimization Guide Part 1**: https://blogs.intuit.com/2025/08/11/best-practices-for-intuit-api-optimization-part-1/
- **QuickBooks Online API Best Practices**: https://help.developer.intuit.com/s/article/QuickBooks-Online-API-Best-Practices

### Optimization Strategies
1. Use filters in queries (e.g., `WHERE TxnDate > '2025-01-01'`)
2. Set MAXRESULTS to 1000 for maximum efficiency
3. Implement webhooks instead of polling
4. Use Change Data Capture (CDC) for tracking changes
5. Batch operations for bulk updates
6. Cache frequently accessed data

*Sources: [QuickBooks API Essentials](https://rollout.com/integration-guides/quickbooks/api-essentials), [Intuit API Optimization](https://blogs.intuit.com/2025/08/11/best-practices-for-intuit-api-optimization-part-1/)*

---

## Webhooks

### Webhook Configuration
- Configure through QuickBooks Developer Portal
- Supports aggregated notifications (configurable interval)
- Default interval: 5 minutes
- Requires OAuth 2.0 authentication

### Supported Entities for Webhooks
- Customer
- Invoice
- Payment
- Vendor
- Bill
- And many more accounting entities

### Webhook Payload Structure
```json
{
  "eventNotifications": [{
    "realmId": "123456",
    "dataChangeEvent": {
      "entities": [{
        "name": "Customer",
        "id": "1",
        "operation": "Update",
        "lastUpdated": "2025-01-15T10:30:00Z"
      }]
    }
  }]
}
```

### Webhook Security
- Verify webhook signatures using `intuit-signature` header
- Implement proper validation before processing
- Use HTTPS endpoints only

*Sources: [QuickBooks API Essentials](https://rollout.com/integration-guides/quickbooks/api-essentials), [QuickBooks API Integration Guide](https://www.getknit.dev/blog/quickbooks-online-api-integration-guide-in-depth)*

---

## Error Handling

### Error Code Documentation
- **Error Codes Reference**: https://developer.intuit.com/app/developer/qbo/docs/develop/troubleshooting/error-codes
- **Common Errors Guide**: https://developer.intuit.com/app/developer/qbo/docs/develop/troubleshooting/handling-common-errors

### Common Error Codes & Solutions

| Error Code | Message | Solution |
|------------|---------|----------|
| 120 | Authorization Failure | Reconnect app, verify admin status |
| 401 | Unauthorized | Refresh access token |
| 429 | Too Many Requests | Implement rate limiting, add delays |
| 500 | Stale Object Error | Use latest syncToken |
| 610 | Object Not Found | Ensure referenced objects are active |
| 2020 | Required Parameter Missing | Include all required parameters |
| 3200 | Business Validation Error | Check business rules compliance |
| 6000 | Business Validation Error | Set TotalAmt for payments |

### Error Handling Best Practices
1. Cache ID, SyncToken, and Active status for all objects
2. Use webhooks or CDC to track changes
3. Always use latest syncToken for updates
4. Implement exponential backoff for retries
5. Log all errors with full context

*Sources: [Fix QuickBooks API Errors](https://www.dancingnumbers.com/quickbooks-online-accounting-api-errors/), [QuickBooks API Error Codes](https://www.dancingnumbers.com/quickbooks-errors/qb-online/accounting-api-error/)*

---

## Developer Tools & Utilities

### Official Tools
- **API Explorer**: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account
- **OAuth Playground**: https://developer.intuit.com/app/developer/playground
- **Sandbox Company**: Available in developer account for testing
- **API Status Page**: https://developer.intuit.com/api-status

### Development Resources
- **Intuit Developer Support**: https://help.developer.intuit.com/s/
- **Developer Community Forum**: https://help.developer.intuit.com/s/
- **Sample Apps**: Available in GitHub under Intuit organization

### Testing Tools
- Sandbox environment with test data
- OAuth 2.0 playground for token testing
- API Explorer for endpoint testing
- Postman collections available

*Sources: [QuickBooks Developer Portal](https://developer.intuit.com), [Intuit Developer Support](https://help.developer.intuit.com/s/)*

---

## Community Resources & Tutorials

### Third-Party Tutorials & Guides
1. **QuickBooks OAuth Setup Guide** (Stateful):
   - URL: https://stateful.com/blog/quickbooks-oauth
   - Focus: Node.js OAuth implementation

2. **QuickBooks API Integration Guide** (Zuplo):
   - URL: https://zuplo.com/blog/2025/05/20/quickbooks-api
   - Focus: Enterprise integration patterns

3. **In-Depth Integration Guide** (Knit):
   - URL: https://www.getknit.dev/blog/quickbooks-online-api-integration-guide-in-depth
   - Focus: Comprehensive integration workflow

4. **QuickBooks API with Python** (Stack Overflow):
   - URL: https://stackoverflow.com/questions/30605128/python-with-quickbooks-online-api-v3
   - Focus: Python implementation examples

### Alternative Integration Services
- **Conductor** (QuickBooks Desktop): https://conductor.is/
- **Apideck** (Unified API): https://www.apideck.com/connectors/quickbooks
- **Merge** (Unified API): https://www.merge.dev/blog/quickbooks-api

*Sources: Various community resources and integration platforms*

---

## API Limitations & Workarounds

### Known Limitations

#### 1. Tags API
- **Limitation**: Can retrieve/list tags but cannot create, update, or delete via API
- **Workaround**: Use RPA solutions or manual processes
- **Source**: [QuickBooks API Limitations](https://satvasolutions.com/blog/top-5-quickbooks-api-limitations-to-know-before-developing-qbo-app)

#### 2. Custom Fields
- **Limitation**: Limited support for custom fields in transactions
- **Workaround**: Use memo fields or external database mapping

#### 3. Bank Feeds
- **Limitation**: No direct API access to bank feeds
- **Workaround**: Use bank's API directly or third-party services

#### 4. Bill Payment Fields
- **Limitation**: Some fields not accessible via API
- **Workaround**: RPA with Microsoft Power Automate

#### 5. Projects API
- **Limitation**: No dedicated Projects API (uses sub-customers)
- **Workaround**: Use IsProject flag on Customer entity

### Minor Version Support
- **Current**: Versions 1-74 deprecated as of August 1, 2025
- **Recommended**: Use latest minor version (70+)
- **Documentation**: Check release notes for version-specific features

*Sources: [QuickBooks API Limitations](https://satvasolutions.com/blog/top-5-quickbooks-api-limitations-to-know-before-developing-qbo-app), [QuickBooks API Best Practices](https://help.developer.intuit.com/s/article/QuickBooks-Online-API-Best-Practices)*

---

## Quick Reference URLs

### Essential Links
```
Developer Portal: https://developer.intuit.com
API Status: https://developer.intuit.com/api-status
OAuth Playground: https://developer.intuit.com/app/developer/playground
API Explorer: https://developer.intuit.com/apiexplorer
Support: https://help.developer.intuit.com/s/
Documentation: https://developer.intuit.com/app/developer/qbo/docs/develop
```

### API Base URLs
```
Production: https://quickbooks.api.intuit.com/v3/company/{realmId}/
Sandbox: https://sandbox-quickbooks.api.intuit.com/v3/company/{realmId}/
OAuth: https://oauth.platform.intuit.com/oauth2/v1/
```

---

## Additional Notes for Developers

1. **Always use HTTPS** - HTTP is not supported for security
2. **Store tokens securely** - Use encryption for refresh tokens
3. **Implement proper error handling** - QBO errors can be cryptic
4. **Use webhooks when possible** - Reduces API calls and improves real-time updates
5. **Test in sandbox first** - Production has stricter requirements
6. **Monitor API usage** - Stay within rate limits
7. **Keep SDKs updated** - New features and bug fixes
8. **Use batch operations** - For bulk data operations
9. **Implement Change Data Capture** - For efficient syncing
10. **Document your integration** - For maintenance and troubleshooting

---

*This document compiled from official QuickBooks documentation and verified community resources as of August 2025. Always check official documentation for the most current information.*