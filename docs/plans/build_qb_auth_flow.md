# QuickBooks Authentication Flow Implementation Plan

## Overview
This document outlines the complete user flow for a brand new user to:
1. Create an account in our application
2. Authorize QuickBooks integration
3. Collect and save OAuth tokens
4. Return to the home screen with active QuickBooks connection

## User Journey Map

### Phase 1: User Registration & Onboarding

#### Step 1.1: Landing Page
**URL**: `/`
- User arrives at the application landing page
- Sees "Get Started" or "Sign Up" CTA button
- Clear value proposition about QuickBooks integration

#### Step 1.2: Account Registration
**URL**: `/account/register`
- User provides:
  - Email address
  - Password (with confirmation)
  - First and Last name
  - Company/Organization name
- Accepts Terms of Service
- Submits registration form

#### Step 1.3: Email Verification
**URL**: `/account/verify-email?token=xxx`
- System sends verification email
- User clicks verification link
- Account marked as verified
- Redirected to onboarding flow

#### Step 1.4: Organization Setup
**URL**: `/onboarding/organization`
- User creates or joins an organization
- Fields:
  - Organization name
  - Organization type (business type)
  - Team size
  - Subscription tier selection (free/starter/pro)
- Organization record created in database

### Phase 2: QuickBooks Authorization

#### Step 2.1: Integration Prompt
**URL**: `/onboarding/integrations`
- After organization setup, user sees integration options
- QuickBooks prominently featured with benefits:
  - "Sync your financial data automatically"
  - "Generate reports with AI assistance"
  - "Automate invoice creation"
- "Connect QuickBooks" button displayed

#### Step 2.2: Pre-Authorization Check
**URL**: `/api/quickbooks/pre-connect`
- System checks:
  - User is authenticated
  - Organization exists and is active
  - No existing active QuickBooks connection
- Generates and stores OAuth state parameter in session
- Returns authorization URL

#### Step 2.3: QuickBooks OAuth Authorization
**URL**: `https://appcenter.intuit.com/connect/oauth2`
- User redirected to QuickBooks OAuth page
- Parameters sent:
  - `client_id`: From settings.QUICKBOOKS_CLIENT_ID
  - `scope`: "com.intuit.quickbooks.accounting"
  - `redirect_uri`: "https://ourapp.com/api/quickbooks/callback"
  - `response_type`: "code"
  - `state`: Random UUID for CSRF protection
- User logs into QuickBooks account
- User selects company to connect
- User authorizes our application

#### Step 2.4: OAuth Callback Handler
**URL**: `/api/quickbooks/callback?code=xxx&state=yyy&realmId=zzz`
- Verify state parameter matches session
- Exchange authorization code for tokens:
  ```
  POST https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
  ```
- Receive:
  - Access token (expires in 1 hour)
  - Refresh token (expires in 100 days)
  - Token expiry time
- Save to database

#### Step 2.5: Token Storage
**Database Operations**:
```python
QuickBooksConnection.objects.create(
    organization=user.organization,
    company_id=request.GET['realmId'],
    company_name=company_info['CompanyName'],
    access_token=encrypted(tokens['access_token']),
    refresh_token=encrypted(tokens['refresh_token']),
    token_expires_at=calculate_expiry(tokens['expires_in']),
    is_active=True,
    last_sync_at=None
)
```

#### Step 2.6: Initial Data Sync
**Background Task**:
- Fetch company information
- Cache frequently used data:
  - Customer list
  - Item/Product catalog
  - Recent invoices
- Update last_sync_at timestamp

### Phase 3: Post-Connection Experience

#### Step 3.1: Success Confirmation
**URL**: `/onboarding/success`
- Display success message:
  - "QuickBooks Connected Successfully!"
  - Show connected company name
  - Display sync status
- Offer quick actions:
  - "View Dashboard"
  - "Import Data"
  - "Create First Invoice"

#### Step 3.2: Dashboard Redirect
**URL**: `/dashboard` or `/`
- User lands on main dashboard
- QuickBooks connection status indicator visible
- Quick stats from QuickBooks displayed:
  - Outstanding invoices count
  - Recent payments
  - Customer count
- MCP tools available for AI assistance

## Technical Implementation Details

### Required Components

#### 1. Models (apps/integration/models/quickbooks.py)
**✅ Status: Models created and migrated successfully**

The following models have been implemented in `apps/integration/models/quickbooks.py`:
- `Organization` - B2B organization management
- `QuickBooksConnection` - OAuth token storage and connection tracking  
- `MCPSession` - MCP protocol session management

```python
class QuickBooksConnection(Timestampable, models.Model):
    organization = models.ForeignKey(Organization, ...)
    company_id = models.CharField(max_length=255)  # QuickBooks realmId
    company_name = models.CharField(max_length=255)
    
    # OAuth tokens (encrypted at rest)
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_expires_at = models.DateTimeField()
    
    # Connection metadata
    is_active = models.BooleanField(default=True)
    is_sandbox = models.BooleanField(default=False)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    
    # Webhook verification
    webhook_token = models.CharField(max_length=255, blank=True)
```

#### 2. Views (apps/integration/quickbooks/views.py)
```python
class QuickBooksConnectView(LoginRequiredMixin, View):
    """Initiates QuickBooks OAuth flow"""
    def get(self, request):
        # Generate state parameter
        # Build authorization URL
        # Store state in session
        # Redirect to QuickBooks
        
class QuickBooksCallbackView(LoginRequiredMixin, View):
    """Handles OAuth callback from QuickBooks"""
    def get(self, request):
        # Verify state parameter
        # Exchange code for tokens
        # Save connection to database
        # Trigger initial sync
        # Redirect to success page

class QuickBooksDisconnectView(LoginRequiredMixin, View):
    """Disconnects QuickBooks integration"""
    def post(self, request):
        # Revoke tokens with QuickBooks
        # Mark connection as inactive
        # Clear cached data
```

#### 3. OAuth Client (apps/integration/quickbooks/oauth.py)
Note: Import models from `apps.integration.models.quickbooks`
```python
class QuickBooksOAuthClient:
    """Handles OAuth 2.0 flow with QuickBooks"""
    
    def get_authorization_url(self, state: str) -> str:
        """Build QuickBooks authorization URL"""
        
    def exchange_code_for_tokens(self, code: str) -> dict:
        """Exchange authorization code for access/refresh tokens"""
        
    def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh expired access token"""
        
    def revoke_tokens(self, token: str) -> bool:
        """Revoke access/refresh tokens"""
```

#### 4. User Registration Views (apps/public/views/account.py)
```python
class RegisterView(View):
    """User registration with organization creation"""
    template_name = "account/register.html"
    
    def post(self, request):
        # Create user account
        # Send verification email
        # Create organization
        # Log user in
        # Redirect to onboarding

class OnboardingView(LoginRequiredMixin, View):
    """Onboarding flow with QuickBooks prompt"""
    template_name = "onboarding/quickbooks.html"
    
    def get(self, request):
        # Show QuickBooks connection benefits
        # Display connect button
```

#### 5. Templates

**account/register.html**:
- Registration form with HTMX validation
- Password strength indicator
- Terms acceptance checkbox

**onboarding/quickbooks.html**:
- Benefits of QuickBooks integration
- Visual connection flow diagram
- Clear CTA to connect

**onboarding/success.html**:
- Success confirmation
- Connected company details
- Next steps guidance

**components/quickbooks_status.html**:
- Connection status indicator
- Last sync time
- Quick actions dropdown

### Security Considerations

#### Token Storage
- Encrypt tokens at rest using django-cryptography
- Store tokens in separate table from user data
- Implement token rotation on refresh

#### CSRF Protection
- Use state parameter in OAuth flow
- Validate state on callback
- Time-limit state validity (15 minutes)

#### Access Control
- Require authenticated user
- Verify organization membership
- Check subscription limits

#### API Rate Limiting
- Track API calls per organization
- Implement exponential backoff
- Cache frequently accessed data

### Error Handling

#### OAuth Flow Errors
- **User denies authorization**: Show friendly message, offer retry
- **Invalid state parameter**: Log security event, show error
- **Token exchange fails**: Log error, show retry option
- **Network timeout**: Implement retry with backoff

#### Connection Errors
- **Token expired**: Auto-refresh using refresh token
- **Refresh token expired**: Prompt re-authorization
- **API rate limit**: Queue requests, notify user
- **Company not found**: Clear connection, prompt re-connect

### User Experience Optimizations

#### Progressive Disclosure
1. Start with minimal required fields
2. Add organization details after email verification
3. Offer QuickBooks as optional enhancement
4. Guide through connection with clear benefits

#### Visual Feedback
- Loading states during OAuth redirect
- Progress indicators for data sync
- Success animations on connection
- Status badges in dashboard

#### Fallback Options
- Allow skip QuickBooks during onboarding
- Provide manual data entry option
- Enable connection later from settings
- Support multiple QuickBooks accounts

## Implementation Timeline

### Phase 1: Core Authentication (Week 1)
- [ ] User registration view and form
- [ ] Email verification system
- [ ] Organization model and creation
- [ ] Basic login/logout flow

### Phase 2: OAuth Implementation (Week 2)
- [ ] QuickBooks OAuth client
- [ ] Connect and callback views
- [ ] Token storage with encryption
- [ ] Token refresh mechanism

### Phase 3: UI/UX Polish (Week 3)
- [ ] Onboarding flow templates
- [ ] HTMX interactions
- [ ] Loading states and animations
- [ ] Error handling and messages

### Phase 4: Integration Features (Week 4)
- [ ] Initial data sync
- [ ] Dashboard widgets
- [ ] Connection status monitoring
- [ ] Webhook support

## Testing Strategy

### Unit Tests
- OAuth client methods
- Token encryption/decryption
- Model validations
- View permissions

### Integration Tests
- Full OAuth flow simulation
- Token refresh scenarios
- Error handling paths
- Multi-user/organization scenarios

### E2E Tests
- Complete user registration
- QuickBooks connection flow
- Dashboard data display
- Disconnection and reconnection

### Security Tests
- CSRF protection validation
- Token storage encryption
- Rate limiting enforcement
- Access control verification

## Monitoring & Analytics

### Key Metrics to Track
- Registration completion rate
- QuickBooks connection rate
- OAuth error frequency
- Token refresh success rate
- API call volume per organization

### Logging Requirements
- All OAuth events
- Token refresh attempts
- API errors and retries
- Security-related events

### User Feedback Collection
- Post-connection survey
- Error experience feedback
- Feature request tracking
- Support ticket integration

## Documentation Requirements

### User Documentation
- Step-by-step connection guide
- Troubleshooting common issues
- FAQ section
- Video walkthrough

### Developer Documentation
- API endpoint specifications
- OAuth flow sequence diagram
- Error codes and handling
- Testing instructions

### Internal Documentation
- Security protocols
- Token management procedures
- Incident response plan
- Monitoring dashboard guide

## Success Criteria

### Functional Requirements
- ✅ User can create account and verify email
- ✅ User can create/join organization
- ✅ User can authorize QuickBooks access
- ✅ Tokens are securely stored and refreshed
- ✅ User sees QuickBooks data in dashboard

### Performance Requirements
- Registration under 30 seconds
- OAuth flow under 15 seconds
- Token refresh under 2 seconds
- Dashboard load under 3 seconds

### Security Requirements
- All tokens encrypted at rest
- HTTPS for all endpoints
- State parameter validation
- Rate limiting enforced

### User Experience Requirements
- Mobile-responsive design
- Clear error messages
- Progress indicators
- Accessible UI (WCAG 2.1 AA)

## Next Steps

1. **Review and Approval**: Get stakeholder sign-off on flow
2. **Design Mockups**: Create UI/UX designs for each step
3. **Database Migrations**: Create required models
4. **Implementation**: Build components in phases
5. **Testing**: Comprehensive test coverage
6. **Documentation**: User and developer guides
7. **Deployment**: Staged rollout with monitoring
8. **Iteration**: Gather feedback and improve

## Appendix

### QuickBooks OAuth URLs
- **Authorization**: `https://appcenter.intuit.com/connect/oauth2`
- **Token Exchange**: `https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer`
- **Token Revoke**: `https://developer.api.intuit.com/v2/oauth2/tokens/revoke`
- **User Info**: `https://accounts.platform.intuit.com/v1/openid_connect/userinfo`

### Required Environment Variables
```env
QUICKBOOKS_CLIENT_ID=your_client_id
QUICKBOOKS_CLIENT_SECRET=your_client_secret
QUICKBOOKS_REDIRECT_URI=https://yourapp.com/api/quickbooks/callback
QUICKBOOKS_WEBHOOK_TOKEN=random_webhook_verification_token
QUICKBOOKS_SANDBOX_MODE=True  # False for production
```

### Database Schema Changes
**✅ Status: Database migrations completed**

The following tables have been created via Django migrations:
- `integration_organization` 
- `integration_quickbooksconnection`
- `integration_mcpsession`

```sql
-- Tables created by migration integration.0001_initial
CREATE TABLE organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    slug VARCHAR(255) UNIQUE,
    subscription_tier VARCHAR(50),
    created_at TIMESTAMP,
    modified_at TIMESTAMP
);

CREATE TABLE organization_members (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    user_id INTEGER REFERENCES users(id),
    role VARCHAR(50),
    joined_at TIMESTAMP
);

CREATE TABLE quickbooks_connections (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    company_id VARCHAR(255),
    company_name VARCHAR(255),
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP,
    modified_at TIMESTAMP
);
```

This comprehensive plan provides a complete roadmap for implementing QuickBooks authentication from user registration through successful connection and data display.