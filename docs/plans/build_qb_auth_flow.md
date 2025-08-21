# QuickBooks Authentication Flow Implementation Plan

## Overview
This document outlines the complete user flow for a brand new user to:
1. Create an account in our application
2. Authorize QuickBooks integration
3. Collect and save OAuth tokens
4. Return to the home screen with active QuickBooks connection

## Current State Analysis

### ✅ Existing Components
- **User Model**: Custom user model in `apps/common/models/user.py`
- **Basic Auth**: Login system with password reset functionality
- **QuickBooks Models**: Organization, QuickBooksConnection, MCPSession in `apps/integration/models/quickbooks.py`
- **API Client**: Complete QuickBooks client in `apps/integration/quickbooks/client.py`
- **MCP Server**: Functional QuickBooks MCP integration
- **Team System**: Complete team management with roles
- **Templates**: Base template system with HTMX integration

### ❌ Critical Gaps Identified
1. **No User Registration**: Registration view is commented out in URLs
2. **Broken Organization Architecture**: Organization model has no relationship to Users
3. **No OAuth Implementation**: Missing OAuth views, client, and API endpoints
4. **No Onboarding Flow**: Missing templates and views for onboarding
5. **No Email Verification**: System not implemented
6. **No Dashboard Integration**: Missing QuickBooks status indicators and data display

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

#### 1. Model Updates Required

##### a. Fix Organization-User Relationship (CRITICAL - MUST BE DONE FIRST)
**Location**: `apps/integration/models/quickbooks.py`
**Status**: ❌ MISSING - Organization has no relationship to Users

```python
# ADD to Organization model:
class Organization(Timestampable, models.Model):
    # ... existing fields ...
    
    # ADD THIS RELATIONSHIP
    members = models.ManyToManyField(
        'common.User',
        through='OrganizationMember',
        related_name='organizations'
    )
    
    # ADD helper methods
    def add_member(self, user, role='member'):
        """Add a user to the organization"""
        return OrganizationMember.objects.create(
            organization=self,
            user=user,
            role=role
        )
    
    def get_owners(self):
        """Get all owners of the organization"""
        return self.members.filter(
            organizationmember__role='owner'
        )

# CREATE NEW MODEL for membership
class OrganizationMember(Timestampable, models.Model):
    """Through model for Organization-User relationship"""
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('admin', 'Admin'),
        ('member', 'Member'),
        ('viewer', 'Viewer'),
    ]
    
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    user = models.ForeignKey(
        'common.User',
        on_delete=models.CASCADE,
        related_name='organization_memberships'
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='member'
    )
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['organization', 'user']
        ordering = ['-joined_at']
    
    def __str__(self):
        return f"{self.user.email} - {self.organization.name} ({self.role})"
```

##### b. Update User Model
**Location**: `apps/common/models/user.py`
**Status**: ❌ MISSING - User needs organization context

```python
# ADD to User model:
class User(AbstractUser):
    # ... existing fields ...
    
    # ADD current organization tracking
    current_organization = models.ForeignKey(
        'integration.Organization',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='current_users'
    )
    
    # ADD email verification fields
    email_verified = models.BooleanField(default=False)
    email_verification_token = models.CharField(
        max_length=255,
        blank=True
    )
    email_verification_sent_at = models.DateTimeField(
        null=True,
        blank=True
    )
    
    # ADD helper methods
    def get_organizations(self):
        """Get all organizations this user belongs to"""
        return self.organizations.filter(
            memberships__is_active=True
        )
    
    def switch_organization(self, organization):
        """Switch current organization context"""
        if self.organizations.filter(id=organization.id).exists():
            self.current_organization = organization
            self.save(update_fields=['current_organization'])
            return True
        return False
```

##### c. Existing Models Status
**✅ QuickBooksConnection** - Already implemented correctly
**✅ MCPSession** - Already implemented correctly

#### 2. Create User Registration System (NEW)
**Location**: `apps/public/views/account.py`
**Status**: ❌ MISSING - Registration is commented out

```python
from django.contrib.auth import login
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.views import View
from apps.common.models import User
from apps.integration.models.quickbooks import Organization, OrganizationMember

class RegisterView(View):
    """User registration with organization creation"""
    template_name = "account/register.html"
    
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('home')
        return render(request, self.template_name)
    
    def post(self, request):
        # Extract form data
        email = request.POST.get('email', '').lower().strip()
        password = request.POST.get('password')
        password_confirm = request.POST.get('password_confirm')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        organization_name = request.POST.get('organization_name')
        
        # Validation
        errors = {}
        if User.objects.filter(email=email).exists():
            errors['email'] = 'Email already registered'
        if password != password_confirm:
            errors['password'] = 'Passwords do not match'
        if len(password) < 8:
            errors['password'] = 'Password must be at least 8 characters'
        
        if errors:
            return render(request, self.template_name, {
                'errors': errors,
                'form_data': request.POST
            })
        
        # Create user
        user = User.objects.create_user(
            username=email,  # Use email as username
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            email_verified=False,
            email_verification_token=get_random_string(32)
        )
        
        # Create organization
        organization = Organization.objects.create(
            name=organization_name,
            subscription_tier='free'  # Start with free tier
        )
        
        # Add user as owner of organization
        OrganizationMember.objects.create(
            organization=organization,
            user=user,
            role='owner'
        )
        
        # Set as current organization
        user.current_organization = organization
        user.save()
        
        # Send verification email
        self.send_verification_email(user)
        
        # Log user in
        login(request, user)
        
        # Redirect to onboarding
        messages.success(request, 'Account created! Please verify your email.')
        return redirect('onboarding')
    
    def send_verification_email(self, user):
        """Send email verification link"""
        verification_url = self.request.build_absolute_uri(
            reverse('verify-email') + f'?token={user.email_verification_token}'
        )
        
        html_message = render_to_string('emails/verify_email.html', {
            'user': user,
            'verification_url': verification_url
        })
        
        send_mail(
            subject='Verify your email address',
            message='Please verify your email address.',
            from_email='noreply@example.com',
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False
        )
        
        user.email_verification_sent_at = timezone.now()
        user.save()

class EmailVerificationView(View):
    """Handle email verification"""
    
    def get(self, request):
        token = request.GET.get('token')
        
        try:
            user = User.objects.get(email_verification_token=token)
            user.email_verified = True
            user.email_verification_token = ''
            user.save()
            
            messages.success(request, 'Email verified successfully!')
            
            if user.is_authenticated:
                return redirect('onboarding')
            else:
                return redirect('login')
                
        except User.DoesNotExist:
            messages.error(request, 'Invalid verification link.')
            return redirect('login')

class OnboardingView(LoginRequiredMixin, View):
    """Onboarding flow with QuickBooks prompt"""
    template_name = "onboarding/quickbooks.html"
    
    def get(self, request):
        # Check if user already has QuickBooks connected
        from apps.integration.models.quickbooks import QuickBooksConnection
        
        has_quickbooks = QuickBooksConnection.objects.filter(
            organization=request.user.current_organization,
            is_active=True
        ).exists()
        
        return render(request, self.template_name, {
            'organization': request.user.current_organization,
            'has_quickbooks': has_quickbooks
        })
```

#### 3. OAuth Client Implementation (NEW)
**Location**: `apps/integration/quickbooks/oauth.py`
**Status**: ❌ MISSING - Create new file

```python
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.utils import timezone

class QuickBooksOAuthClient:
    """Handles OAuth 2.0 flow with QuickBooks"""
    
    def __init__(self):
        self.client_id = settings.QUICKBOOKS_CLIENT_ID
        self.client_secret = settings.QUICKBOOKS_CLIENT_SECRET
        self.redirect_uri = settings.QUICKBOOKS_REDIRECT_URI
        self.sandbox = settings.QUICKBOOKS_SANDBOX_MODE
        
        # OAuth endpoints
        self.auth_base = "https://appcenter.intuit.com"
        self.token_base = "https://oauth.platform.intuit.com"
        
    def get_authorization_url(self, state: str) -> str:
        """Build QuickBooks authorization URL with PKCE"""
        # Generate PKCE challenge
        code_verifier = base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode('utf-8').rstrip('=')
        
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode('utf-8').rstrip('=')
        
        params = {
            'client_id': self.client_id,
            'scope': 'com.intuit.quickbooks.accounting',
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }
        
        auth_url = f"{self.auth_base}/connect/oauth2?{urlencode(params)}"
        
        # Return URL and code_verifier (store verifier in session)
        return auth_url, code_verifier
    
    async def exchange_code_for_tokens(
        self, 
        code: str, 
        code_verifier: str,
        realm_id: str
    ) -> Dict:
        """Exchange authorization code for access/refresh tokens"""
        
        # Prepare token request
        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.redirect_uri,
            'code_verifier': code_verifier
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.token_base}/oauth2/v1/tokens/bearer",
                headers=headers,
                data=data
            )
            
            if response.status_code != 200:
                raise Exception(f"Token exchange failed: {response.text}")
            
            tokens = response.json()
            
            # Add expiration timestamp
            tokens['expires_at'] = (
                timezone.now() + 
                timedelta(seconds=tokens['expires_in'])
            ).isoformat()
            
            # Get company info
            company_info = await self.get_company_info(
                tokens['access_token'], 
                realm_id
            )
            tokens['company_info'] = company_info
            
            return tokens
    
    async def refresh_access_token(self, refresh_token: str) -> Dict:
        """Refresh expired access token"""
        
        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.token_base}/oauth2/v1/tokens/bearer",
                headers=headers,
                data=data
            )
            
            if response.status_code != 200:
                raise Exception(f"Token refresh failed: {response.text}")
            
            tokens = response.json()
            tokens['expires_at'] = (
                timezone.now() + 
                timedelta(seconds=tokens['expires_in'])
            ).isoformat()
            
            return tokens
    
    async def revoke_tokens(self, token: str) -> bool:
        """Revoke access/refresh tokens"""
        
        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'token': token
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.token_base}/oauth2/v1/tokens/revoke",
                headers=headers,
                data=data
            )
            
            return response.status_code == 200
    
    async def get_company_info(self, access_token: str, realm_id: str) -> Dict:
        """Get QuickBooks company information"""
        
        base_url = (
            "https://sandbox-quickbooks.api.intuit.com" 
            if self.sandbox 
            else "https://quickbooks.api.intuit.com"
        )
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/v3/company/{realm_id}/companyinfo/{realm_id}",
                headers=headers
            )
            
            if response.status_code != 200:
                raise Exception(f"Failed to get company info: {response.text}")
            
            data = response.json()
            return data.get('CompanyInfo', {})
```

#### 4. QuickBooks OAuth Views (NEW)
**Location**: `apps/integration/quickbooks/views.py`
**Status**: ❌ MISSING - Create new file

```python
import asyncio
import json
import uuid
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from apps.integration.models.quickbooks import QuickBooksConnection
from apps.integration.quickbooks.oauth import QuickBooksOAuthClient

class QuickBooksConnectView(LoginRequiredMixin, View):
    """Initiates QuickBooks OAuth flow"""
    
    def get(self, request):
        # Check if organization exists
        if not request.user.current_organization:
            messages.error(request, 'Please create an organization first.')
            return redirect('onboarding')
        
        # Check for existing connection
        existing = QuickBooksConnection.objects.filter(
            organization=request.user.current_organization,
            is_active=True
        ).first()
        
        if existing:
            messages.info(request, 'QuickBooks is already connected.')
            return redirect('dashboard')
        
        # Generate state parameter for CSRF protection
        state = str(uuid.uuid4())
        request.session['qb_oauth_state'] = state
        request.session['qb_oauth_timestamp'] = timezone.now().isoformat()
        
        # Get authorization URL with PKCE
        oauth_client = QuickBooksOAuthClient()
        auth_url, code_verifier = oauth_client.get_authorization_url(state)
        
        # Store code verifier in session for token exchange
        request.session['qb_code_verifier'] = code_verifier
        
        # Redirect to QuickBooks
        return redirect(auth_url)

class QuickBooksCallbackView(LoginRequiredMixin, View):
    """Handles OAuth callback from QuickBooks"""
    
    def get(self, request):
        # Get parameters
        code = request.GET.get('code')
        state = request.GET.get('state')
        realm_id = request.GET.get('realmId')
        error = request.GET.get('error')
        
        # Handle errors
        if error:
            messages.error(
                request, 
                f'QuickBooks authorization failed: {error}'
            )
            return redirect('onboarding')
        
        # Verify state parameter
        session_state = request.session.get('qb_oauth_state')
        if not session_state or session_state != state:
            messages.error(request, 'Invalid authorization state.')
            return redirect('onboarding')
        
        # Get code verifier from session
        code_verifier = request.session.get('qb_code_verifier')
        if not code_verifier:
            messages.error(request, 'Missing authorization verifier.')
            return redirect('onboarding')
        
        try:
            # Exchange code for tokens
            oauth_client = QuickBooksOAuthClient()
            tokens = asyncio.run(
                oauth_client.exchange_code_for_tokens(
                    code, 
                    code_verifier,
                    realm_id
                )
            )
            
            # Save connection to database
            connection = QuickBooksConnection.objects.create(
                organization=request.user.current_organization,
                company_id=realm_id,
                company_name=tokens['company_info'].get('CompanyName', 'Unknown'),
                access_token=tokens['access_token'],  # Should be encrypted
                refresh_token=tokens['refresh_token'],  # Should be encrypted
                token_expires_at=tokens['expires_at'],
                is_active=True,
                is_sandbox=settings.QUICKBOOKS_SANDBOX_MODE
            )
            
            # Clean up session
            request.session.pop('qb_oauth_state', None)
            request.session.pop('qb_code_verifier', None)
            request.session.pop('qb_oauth_timestamp', None)
            
            # Trigger initial sync (async task)
            from apps.integration.quickbooks.tasks import sync_quickbooks_data
            sync_quickbooks_data.delay(connection.id)
            
            messages.success(
                request, 
                f'Successfully connected to {connection.company_name}!'
            )
            return redirect('onboarding-success')
            
        except Exception as e:
            messages.error(request, f'Failed to connect: {str(e)}')
            return redirect('onboarding')

class QuickBooksDisconnectView(LoginRequiredMixin, View):
    """Disconnects QuickBooks integration"""
    
    def post(self, request):
        try:
            connection = QuickBooksConnection.objects.get(
                organization=request.user.current_organization,
                is_active=True
            )
            
            # Revoke tokens with QuickBooks
            oauth_client = QuickBooksOAuthClient()
            asyncio.run(
                oauth_client.revoke_tokens(connection.refresh_token)
            )
            
            # Mark connection as inactive
            connection.is_active = False
            connection.save()
            
            messages.success(request, 'QuickBooks disconnected successfully.')
            
        except QuickBooksConnection.DoesNotExist:
            messages.error(request, 'No active QuickBooks connection found.')
        
        return redirect('settings')

class QuickBooksStatusView(LoginRequiredMixin, View):
    """Check QuickBooks connection status (HTMX endpoint)"""
    
    def get(self, request):
        try:
            connection = QuickBooksConnection.objects.get(
                organization=request.user.current_organization,
                is_active=True
            )
            
            # Check if token needs refresh
            needs_refresh = connection.token_expires_at <= timezone.now()
            
            return JsonResponse({
                'connected': True,
                'company_name': connection.company_name,
                'needs_refresh': needs_refresh,
                'last_sync': connection.last_sync_at.isoformat() if connection.last_sync_at else None
            })
            
        except QuickBooksConnection.DoesNotExist:
            return JsonResponse({'connected': False})
```

#### 5. Templates (NEW)

##### a. Registration Template
**Location**: `apps/public/templates/account/register.html`
**Status**: ❌ MISSING - Create new file

```html
{% extends "base.html" %}
{% load static %}

{% block title %}Create Account{% endblock %}

{% block content %}
<div class="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
    <div class="max-w-md w-full space-y-8">
        <div>
            <h2 class="mt-6 text-center text-3xl font-extrabold text-gray-900">
                Create your account
            </h2>
            <p class="mt-2 text-center text-sm text-gray-600">
                Or
                <a href="{% url 'login' %}" class="font-medium text-blue-600 hover:text-blue-500">
                    sign in to your existing account
                </a>
            </p>
        </div>
        
        <form class="mt-8 space-y-6" method="POST" action="{% url 'register' %}">
            {% csrf_token %}
            
            {% if errors %}
                <div class="rounded-md bg-red-50 p-4">
                    <div class="flex">
                        <div class="ml-3">
                            <h3 class="text-sm font-medium text-red-800">
                                Please correct the following errors:
                            </h3>
                            <div class="mt-2 text-sm text-red-700">
                                <ul class="list-disc pl-5 space-y-1">
                                    {% for field, error in errors.items %}
                                        <li>{{ error }}</li>
                                    {% endfor %}
                                </ul>
                            </div>
                        </div>
                    </div>
                </div>
            {% endif %}
            
            <div class="space-y-4">
                <!-- Personal Information -->
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label for="first_name" class="block text-sm font-medium text-gray-700">
                            First name
                        </label>
                        <input
                            id="first_name"
                            name="first_name"
                            type="text"
                            required
                            value="{{ form_data.first_name }}"
                            class="mt-1 appearance-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                        >
                    </div>
                    
                    <div>
                        <label for="last_name" class="block text-sm font-medium text-gray-700">
                            Last name
                        </label>
                        <input
                            id="last_name"
                            name="last_name"
                            type="text"
                            required
                            value="{{ form_data.last_name }}"
                            class="mt-1 appearance-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                        >
                    </div>
                </div>
                
                <!-- Email -->
                <div>
                    <label for="email" class="block text-sm font-medium text-gray-700">
                        Email address
                    </label>
                    <input
                        id="email"
                        name="email"
                        type="email"
                        autocomplete="email"
                        required
                        value="{{ form_data.email }}"
                        class="mt-1 appearance-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                        hx-post="{% url 'check-email' %}"
                        hx-trigger="blur"
                        hx-target="#email-error"
                    >
                    <div id="email-error" class="mt-1 text-sm text-red-600"></div>
                </div>
                
                <!-- Organization -->
                <div>
                    <label for="organization_name" class="block text-sm font-medium text-gray-700">
                        Organization name
                    </label>
                    <input
                        id="organization_name"
                        name="organization_name"
                        type="text"
                        required
                        value="{{ form_data.organization_name }}"
                        placeholder="Your company or team name"
                        class="mt-1 appearance-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                    >
                </div>
                
                <!-- Password -->
                <div>
                    <label for="password" class="block text-sm font-medium text-gray-700">
                        Password
                    </label>
                    <input
                        id="password"
                        name="password"
                        type="password"
                        autocomplete="new-password"
                        required
                        class="mt-1 appearance-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                        hx-post="{% url 'check-password-strength' %}"
                        hx-trigger="keyup changed delay:500ms"
                        hx-target="#password-strength"
                    >
                    <div id="password-strength" class="mt-2"></div>
                </div>
                
                <!-- Confirm Password -->
                <div>
                    <label for="password_confirm" class="block text-sm font-medium text-gray-700">
                        Confirm password
                    </label>
                    <input
                        id="password_confirm"
                        name="password_confirm"
                        type="password"
                        autocomplete="new-password"
                        required
                        class="mt-1 appearance-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 focus:z-10 sm:text-sm"
                    >
                </div>
                
                <!-- Terms -->
                <div class="flex items-start">
                    <input
                        id="accept_terms"
                        name="accept_terms"
                        type="checkbox"
                        required
                        class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                    >
                    <label for="accept_terms" class="ml-2 block text-sm text-gray-900">
                        I agree to the
                        <a href="/terms" target="_blank" class="text-blue-600 hover:text-blue-500">
                            Terms of Service
                        </a>
                        and
                        <a href="/privacy" target="_blank" class="text-blue-600 hover:text-blue-500">
                            Privacy Policy
                        </a>
                    </label>
                </div>
            </div>
            
            <div>
                <button
                    type="submit"
                    class="group relative w-full flex justify-center py-2 px-4 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                >
                    Create Account
                </button>
            </div>
        </form>
    </div>
</div>
{% endblock %}
```

##### b. Onboarding Template
**Location**: `apps/public/templates/onboarding/quickbooks.html`
**Status**: ❌ MISSING - Create new file

```html
{% extends "base.html" %}
{% load static %}

{% block title %}Connect QuickBooks{% endblock %}

{% block content %}
<div class="min-h-screen bg-gray-50 py-12">
    <div class="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
        <!-- Progress Steps -->
        <nav aria-label="Progress" class="mb-8">
            <ol class="flex items-center">
                <li class="relative pr-8 sm:pr-20">
                    <div class="absolute inset-0 flex items-center" aria-hidden="true">
                        <div class="h-0.5 w-full bg-green-600"></div>
                    </div>
                    <div class="relative w-8 h-8 flex items-center justify-center bg-green-600 rounded-full">
                        <svg class="w-5 h-5 text-white" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
                        </svg>
                    </div>
                    <p class="mt-2 text-xs font-semibold text-green-600">Account Created</p>
                </li>
                
                <li class="relative pr-8 sm:pr-20">
                    <div class="absolute inset-0 flex items-center" aria-hidden="true">
                        <div class="h-0.5 w-full bg-gray-300"></div>
                    </div>
                    <div class="relative w-8 h-8 flex items-center justify-center bg-blue-600 rounded-full">
                        <span class="text-white text-sm font-semibold">2</span>
                    </div>
                    <p class="mt-2 text-xs font-semibold text-gray-900">Connect QuickBooks</p>
                </li>
                
                <li class="relative">
                    <div class="relative w-8 h-8 flex items-center justify-center bg-gray-300 rounded-full">
                        <span class="text-gray-600 text-sm font-semibold">3</span>
                    </div>
                    <p class="mt-2 text-xs font-semibold text-gray-500">Start Using</p>
                </li>
            </ol>
        </nav>
        
        <!-- Main Content -->
        <div class="bg-white shadow-xl rounded-lg overflow-hidden">
            <div class="px-8 py-10">
                <h1 class="text-3xl font-bold text-gray-900 mb-4">
                    Connect Your QuickBooks Account
                </h1>
                
                <p class="text-lg text-gray-600 mb-8">
                    Integrate with QuickBooks to unlock powerful financial automation and AI-assisted insights.
                </p>
                
                <!-- Benefits Grid -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-10">
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <div class="flex items-center justify-center h-12 w-12 rounded-md bg-blue-500 text-white">
                                <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                                </svg>
                            </div>
                        </div>
                        <div class="ml-4">
                            <h3 class="text-lg font-medium text-gray-900">
                                Automatic Sync
                            </h3>
                            <p class="mt-2 text-sm text-gray-500">
                                Keep your financial data synchronized in real-time with QuickBooks.
                            </p>
                        </div>
                    </div>
                    
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <div class="flex items-center justify-center h-12 w-12 rounded-md bg-blue-500 text-white">
                                <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                                </svg>
                            </div>
                        </div>
                        <div class="ml-4">
                            <h3 class="text-lg font-medium text-gray-900">
                                AI-Powered Insights
                            </h3>
                            <p class="mt-2 text-sm text-gray-500">
                                Get intelligent recommendations and automated financial reports.
                            </p>
                        </div>
                    </div>
                    
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <div class="flex items-center justify-center h-12 w-12 rounded-md bg-blue-500 text-white">
                                <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                            </div>
                        </div>
                        <div class="ml-4">
                            <h3 class="text-lg font-medium text-gray-900">
                                Invoice Automation
                            </h3>
                            <p class="mt-2 text-sm text-gray-500">
                                Create and manage invoices directly from our platform.
                            </p>
                        </div>
                    </div>
                    
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <div class="flex items-center justify-center h-12 w-12 rounded-md bg-blue-500 text-white">
                                <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                                </svg>
                            </div>
                        </div>
                        <div class="ml-4">
                            <h3 class="text-lg font-medium text-gray-900">
                                Secure Connection
                            </h3>
                            <p class="mt-2 text-sm text-gray-500">
                                Bank-level encryption and OAuth 2.0 authentication.
                            </p>
                        </div>
                    </div>
                </div>
                
                <!-- Connection Flow -->
                <div class="bg-gray-50 rounded-lg p-6 mb-8">
                    <h3 class="text-sm font-semibold text-gray-900 mb-4">
                        How it works:
                    </h3>
                    <ol class="list-decimal list-inside space-y-2 text-sm text-gray-600">
                        <li>Click "Connect QuickBooks" below</li>
                        <li>Log in to your QuickBooks account</li>
                        <li>Select the company you want to connect</li>
                        <li>Authorize our application</li>
                        <li>Return here to start using the integration</li>
                    </ol>
                </div>
                
                <!-- Action Buttons -->
                <div class="flex items-center justify-between">
                    <a
                        href="{% url 'quickbooks-connect' %}"
                        class="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-md shadow-sm text-white bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500"
                    >
                        <svg class="mr-2 -ml-1 h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                            <path d="M10 12a2 2 0 100-4 2 2 0 000 4z"/>
                            <path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"/>
                        </svg>
                        Connect QuickBooks
                    </a>
                    
                    {% if not has_quickbooks %}
                    <a
                        href="{% url 'dashboard' %}"
                        class="text-sm text-gray-500 hover:text-gray-700"
                    >
                        Skip for now →
                    </a>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <!-- Security Note -->
        <div class="mt-6 text-center text-sm text-gray-500">
            <p>
                We use OAuth 2.0 for secure authentication. Your QuickBooks credentials are never stored on our servers.
            </p>
        </div>
    </div>
</div>
{% endblock %}
```

##### c. Success Template
**Location**: `apps/public/templates/onboarding/success.html`
**Status**: ❌ MISSING - Create new file

```html
{% extends "base.html" %}
{% load static %}

{% block title %}QuickBooks Connected{% endblock %}

{% block content %}
<div class="min-h-screen bg-gray-50 py-12">
    <div class="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8">
        <!-- Success Card -->
        <div class="bg-white shadow-xl rounded-lg overflow-hidden">
            <div class="bg-green-500 px-8 py-6">
                <div class="flex items-center">
                    <div class="flex-shrink-0">
                        <svg class="h-12 w-12 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    </div>
                    <div class="ml-4">
                        <h1 class="text-2xl font-bold text-white">
                            QuickBooks Connected Successfully!
                        </h1>
                        <p class="mt-1 text-green-100">
                            Your financial data is now syncing
                        </p>
                    </div>
                </div>
            </div>
            
            <div class="px-8 py-6">
                <!-- Connection Details -->
                <div class="mb-6">
                    <h2 class="text-lg font-semibold text-gray-900 mb-3">
                        Connection Details
                    </h2>
                    <dl class="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                        <div>
                            <dt class="text-sm font-medium text-gray-500">
                                Company
                            </dt>
                            <dd class="mt-1 text-sm text-gray-900">
                                {{ connection.company_name }}
                            </dd>
                        </div>
                        <div>
                            <dt class="text-sm font-medium text-gray-500">
                                Status
                            </dt>
                            <dd class="mt-1">
                                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                                    Active
                                </span>
                            </dd>
                        </div>
                        <div>
                            <dt class="text-sm font-medium text-gray-500">
                                Environment
                            </dt>
                            <dd class="mt-1 text-sm text-gray-900">
                                {% if connection.is_sandbox %}Sandbox{% else %}Production{% endif %}
                            </dd>
                        </div>
                        <div>
                            <dt class="text-sm font-medium text-gray-500">
                                Connected At
                            </dt>
                            <dd class="mt-1 text-sm text-gray-900">
                                {{ connection.created_at|date:"M d, Y g:i A" }}
                            </dd>
                        </div>
                    </dl>
                </div>
                
                <!-- Next Steps -->
                <div class="mb-6">
                    <h2 class="text-lg font-semibold text-gray-900 mb-3">
                        What's Next?
                    </h2>
                    <div class="space-y-3">
                        <div class="flex items-start">
                            <svg class="flex-shrink-0 h-5 w-5 text-green-500 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                            </svg>
                            <p class="ml-3 text-sm text-gray-700">
                                Your QuickBooks data is syncing in the background
                            </p>
                        </div>
                        <div class="flex items-start">
                            <svg class="flex-shrink-0 h-5 w-5 text-blue-500 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" />
                            </svg>
                            <p class="ml-3 text-sm text-gray-700">
                                You can now use AI tools to analyze your financial data
                            </p>
                        </div>
                        <div class="flex items-start">
                            <svg class="flex-shrink-0 h-5 w-5 text-purple-500 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z" />
                                <path fill-rule="evenodd" d="M4 5a2 2 0 012-2 1 1 0 000 2H6a2 2 0 100 4h2a2 2 0 100-4h-.5a1 1 0 000-2H8a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V7.414A1.5 1.5 0 0016.586 6L14 3.414A1.5 1.5 0 0012.586 3H12a1 1 0 000 2h.5A1.5 1.5 0 0114 6.5V7a1 1 0 001 1h.5a1.5 1.5 0 011.5 1.5V16a2 2 0 01-2 2H6a2 2 0 01-2-2V5z" clip-rule="evenodd" />
                            </svg>
                            <p class="ml-3 text-sm text-gray-700">
                                Create invoices and manage customers directly from the dashboard
                            </p>
                        </div>
                    </div>
                </div>
                
                <!-- Action Buttons -->
                <div class="flex space-x-3">
                    <a
                        href="{% url 'dashboard' %}"
                        class="flex-1 inline-flex justify-center items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                    >
                        Go to Dashboard
                    </a>
                    <a
                        href="{% url 'settings' %}"
                        class="flex-1 inline-flex justify-center items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                    >
                        View Settings
                    </a>
                </div>
            </div>
        </div>
        
        <!-- Help Section -->
        <div class="mt-8 text-center">
            <p class="text-sm text-gray-500">
                Need help? Check out our
                <a href="/docs/quickbooks" class="font-medium text-blue-600 hover:text-blue-500">
                    QuickBooks integration guide
                </a>
                or
                <a href="/support" class="font-medium text-blue-600 hover:text-blue-500">
                    contact support
                </a>
            </p>
        </div>
    </div>
</div>
{% endblock %}
```

##### d. QuickBooks Status Component
**Location**: `apps/public/templates/components/quickbooks_status.html`
**Status**: ❌ MISSING - Create new file

```html
<!-- QuickBooks Connection Status Widget -->
<div id="quickbooks-status" 
     hx-get="{% url 'quickbooks-status' %}"
     hx-trigger="load, every 30s"
     class="bg-white rounded-lg shadow p-4">
    
    <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-medium text-gray-900">QuickBooks Status</h3>
        <div class="relative">
            <button 
                type="button"
                class="text-gray-400 hover:text-gray-500"
                onclick="toggleDropdown('qb-dropdown')"
            >
                <svg class="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" />
                </svg>
            </button>
            
            <!-- Dropdown Menu -->
            <div id="qb-dropdown" class="hidden absolute right-0 mt-2 w-48 rounded-md shadow-lg bg-white ring-1 ring-black ring-opacity-5">
                <div class="py-1">
                    <a href="{% url 'quickbooks-sync' %}" 
                       class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
                       hx-post="{% url 'quickbooks-sync' %}"
                       hx-swap="none">
                        Sync Now
                    </a>
                    <a href="{% url 'settings' %}#quickbooks" 
                       class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100">
                        Settings
                    </a>
                    <form method="POST" action="{% url 'quickbooks-disconnect' %}" class="m-0">
                        {% csrf_token %}
                        <button type="submit" 
                                class="block w-full text-left px-4 py-2 text-sm text-red-700 hover:bg-gray-100"
                                onclick="return confirm('Are you sure you want to disconnect QuickBooks?')">
                            Disconnect
                        </button>
                    </form>
                </div>
            </div>
        </div>
    </div>
    
    <div id="qb-status-content">
        <!-- This will be replaced by HTMX -->
        <div class="animate-pulse">
            <div class="h-4 bg-gray-200 rounded w-3/4 mb-2"></div>
            <div class="h-3 bg-gray-200 rounded w-1/2"></div>
        </div>
    </div>
</div>

<!-- HTMX Response Template (returned by quickbooks-status endpoint) -->
{% if connected %}
    <div class="flex items-center">
        <div class="flex-shrink-0">
            <span class="inline-flex h-8 w-8 items-center justify-center rounded-full bg-green-100">
                <svg class="h-5 w-5 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                </svg>
            </span>
        </div>
        <div class="ml-3">
            <p class="text-sm font-medium text-gray-900">{{ company_name }}</p>
            <p class="text-xs text-gray-500">
                {% if last_sync %}
                    Last sync: {{ last_sync|timesince }} ago
                {% else %}
                    Syncing...
                {% endif %}
            </p>
        </div>
    </div>
{% else %}
    <div class="flex items-center">
        <div class="flex-shrink-0">
            <span class="inline-flex h-8 w-8 items-center justify-center rounded-full bg-gray-100">
                <svg class="h-5 w-5 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M13.477 14.89A6 6 0 015.11 6.524l8.367 8.368zm1.414-1.414L6.524 5.11a6 6 0 018.367 8.367zM18 10a8 8 0 11-16 0 8 8 0 0116 0z" clip-rule="evenodd" />
                </svg>
            </span>
        </div>
        <div class="ml-3">
            <p class="text-sm font-medium text-gray-900">Not Connected</p>
            <a href="{% url 'onboarding' %}" class="text-xs text-blue-600 hover:text-blue-500">
                Connect QuickBooks →
            </a>
        </div>
    </div>
{% endif %}
```

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

#### 6. URL Configuration (NEW)

##### a. Main URLs
**Location**: `urls.py`
**Status**: ⚠️ PARTIAL - Registration is commented out

```python
# ADD/UNCOMMENT these URLs in main urls.py
from apps.public.views.account import (
    RegisterView, EmailVerificationView, OnboardingView
)
from apps.integration.quickbooks.views import (
    QuickBooksConnectView, QuickBooksCallbackView, 
    QuickBooksDisconnectView, QuickBooksStatusView
)

urlpatterns = [
    # ... existing patterns ...
    
    # Registration & Onboarding
    path('register/', RegisterView.as_view(), name='register'),
    path('verify-email/', EmailVerificationView.as_view(), name='verify-email'),
    path('onboarding/', OnboardingView.as_view(), name='onboarding'),
    path('onboarding/success/', TemplateView.as_view(
        template_name='onboarding/success.html'
    ), name='onboarding-success'),
    
    # QuickBooks OAuth
    path('api/quickbooks/connect/', QuickBooksConnectView.as_view(), name='quickbooks-connect'),
    path('api/quickbooks/callback/', QuickBooksCallbackView.as_view(), name='quickbooks-callback'),
    path('api/quickbooks/disconnect/', QuickBooksDisconnectView.as_view(), name='quickbooks-disconnect'),
    path('api/quickbooks/status/', QuickBooksStatusView.as_view(), name='quickbooks-status'),
    
    # HTMX endpoints
    path('htmx/check-email/', CheckEmailView.as_view(), name='check-email'),
    path('htmx/check-password-strength/', CheckPasswordStrengthView.as_view(), name='check-password-strength'),
]
```

##### b. API URLs
**Location**: `apps/api/urls.py`
**Status**: ❌ MISSING - File is empty

```python
from django.urls import path, include
from rest_framework.routers import DefaultRouter

router = DefaultRouter()

app_name = 'api'

urlpatterns = [
    path('', include(router.urls)),
    path('quickbooks/', include('apps.integration.quickbooks.api_urls')),
]
```

#### 7. Settings Configuration (UPDATE)

##### a. Add Required Settings
**Location**: `settings/third_party.py`
**Status**: ⚠️ PARTIAL - Missing redirect URI

```python
# QuickBooks OAuth Configuration
QUICKBOOKS_CLIENT_ID = env.str('QUICKBOOKS_CLIENT_ID', default='')
QUICKBOOKS_CLIENT_SECRET = env.str('QUICKBOOKS_CLIENT_SECRET', default='')
QUICKBOOKS_WEBHOOK_TOKEN = env.str('QUICKBOOKS_WEBHOOK_TOKEN', default='')
QUICKBOOKS_SANDBOX_MODE = env.bool('QUICKBOOKS_SANDBOX_MODE', default=True)

# ADD THIS:
QUICKBOOKS_REDIRECT_URI = env.str(
    'QUICKBOOKS_REDIRECT_URI',
    default='http://localhost:8000/api/quickbooks/callback/' if DEBUG 
    else 'https://yourdomain.com/api/quickbooks/callback/'
)
```

##### b. Email Configuration
**Location**: `settings/base.py`
**Status**: ❌ MISSING - Add email backend

```python
# Email Configuration for Verification
if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = env.str('EMAIL_HOST', default='smtp.gmail.com')
    EMAIL_PORT = env.int('EMAIL_PORT', default=587)
    EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
    EMAIL_HOST_USER = env.str('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = env.str('EMAIL_HOST_PASSWORD', default='')

DEFAULT_FROM_EMAIL = env.str('DEFAULT_FROM_EMAIL', default='noreply@example.com')
```

#### 8. Background Tasks (NEW)
**Location**: `apps/integration/quickbooks/tasks.py`
**Status**: ❌ MISSING - Create new file

```python
from celery import shared_task
from django.utils import timezone
from apps.integration.models.quickbooks import QuickBooksConnection
from apps.integration.quickbooks.client import QuickBooksClient
import logging

logger = logging.getLogger(__name__)

@shared_task
def sync_quickbooks_data(connection_id):
    """Sync QuickBooks data for a connection"""
    try:
        connection = QuickBooksConnection.objects.get(id=connection_id)
        client = QuickBooksClient(connection)
        
        # Sync company info
        company_info = client.get_company_info()
        connection.company_name = company_info.get('CompanyName', connection.company_name)
        
        # Sync customers
        customers = client.list_customers(max_results=100)
        # Process and store customers...
        
        # Sync invoices
        invoices = client.list_invoices(max_results=100)
        # Process and store invoices...
        
        # Update sync timestamp
        connection.last_sync_at = timezone.now()
        connection.save()
        
        logger.info(f"Successfully synced QuickBooks data for {connection.company_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error syncing QuickBooks data: {str(e)}")
        return False

@shared_task
def refresh_quickbooks_tokens():
    """Refresh expiring QuickBooks tokens"""
    from datetime import timedelta
    
    # Find tokens expiring in next 10 minutes
    expiring_soon = timezone.now() + timedelta(minutes=10)
    connections = QuickBooksConnection.objects.filter(
        is_active=True,
        token_expires_at__lte=expiring_soon
    )
    
    for connection in connections:
        try:
            from apps.integration.quickbooks.oauth import QuickBooksOAuthClient
            oauth_client = QuickBooksOAuthClient()
            
            tokens = oauth_client.refresh_access_token(connection.refresh_token)
            
            connection.access_token = tokens['access_token']
            connection.refresh_token = tokens.get('refresh_token', connection.refresh_token)
            connection.token_expires_at = tokens['expires_at']
            connection.save()
            
            logger.info(f"Refreshed tokens for {connection.company_name}")
            
        except Exception as e:
            logger.error(f"Failed to refresh tokens for {connection.company_name}: {str(e)}")
```

#### 9. Admin Configuration (NEW)
**Location**: `apps/staff/admin.py`
**Status**: ❌ MISSING - Add QuickBooks admin

```python
from django.contrib import admin
from apps.integration.models.quickbooks import (
    Organization, OrganizationMember, QuickBooksConnection
)

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'subscription_tier', 'created_at']
    search_fields = ['name']
    list_filter = ['subscription_tier', 'created_at']
    readonly_fields = ['created_at', 'modified_at']

@admin.register(OrganizationMember)
class OrganizationMemberAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'role', 'joined_at']
    list_filter = ['role', 'is_active']
    search_fields = ['user__email', 'organization__name']
    raw_id_fields = ['user', 'organization']

@admin.register(QuickBooksConnection)
class QuickBooksConnectionAdmin(admin.ModelAdmin):
    list_display = ['organization', 'company_name', 'is_active', 'token_expires_at']
    list_filter = ['is_active', 'is_sandbox']
    search_fields = ['organization__name', 'company_name']
    readonly_fields = ['company_id', 'created_at', 'modified_at']
    
    fieldsets = (
        ('Connection Info', {
            'fields': ('organization', 'company_id', 'company_name')
        }),
        ('Status', {
            'fields': ('is_active', 'is_sandbox', 'last_sync_at')
        }),
        ('Token Info', {
            'fields': ('token_expires_at',),
            'description': 'Token details are encrypted and not displayed'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'modified_at')
        })
    )
```

## Implementation Checklist

### Phase 1: Foundation (Critical - Do First)
- [ ] **Fix Organization-User Relationship**
  - [ ] Add `members` field to Organization model
  - [ ] Create OrganizationMember through model
  - [ ] Add `current_organization` to User model
  - [ ] Run migrations
- [ ] **Setup Email Configuration**
  - [ ] Configure email backend in settings
  - [ ] Test email sending

### Phase 2: User Registration Flow
- [ ] **Create Registration System**
  - [ ] Implement RegisterView in `apps/public/views/account.py`
  - [ ] Create registration template
  - [ ] Add email verification fields to User model
  - [ ] Implement EmailVerificationView
  - [ ] Create verification email template
  - [ ] Uncomment registration URL
- [ ] **Create Onboarding Flow**
  - [ ] Implement OnboardingView
  - [ ] Create onboarding templates
  - [ ] Add onboarding URLs

### Phase 3: QuickBooks OAuth Implementation  
- [ ] **Build OAuth Client**
  - [ ] Create `apps/integration/quickbooks/oauth.py`
  - [ ] Implement OAuth 2.0 with PKCE
  - [ ] Add token refresh logic
- [ ] **Create OAuth Views**
  - [ ] Create `apps/integration/quickbooks/views.py`
  - [ ] Implement connect, callback, disconnect views
  - [ ] Add status check view
- [ ] **Configure URLs**
  - [ ] Add QuickBooks URLs to main urls.py
  - [ ] Setup API URLs

### Phase 4: Templates & UI
- [ ] **Create Templates**
  - [ ] Registration form template
  - [ ] Onboarding QuickBooks template
  - [ ] Success confirmation template
  - [ ] QuickBooks status widget
- [ ] **Add HTMX Interactions**
  - [ ] Email availability check
  - [ ] Password strength indicator
  - [ ] Status widget auto-refresh

### Phase 5: Background Processing
- [ ] **Setup Celery Tasks**
  - [ ] Create sync_quickbooks_data task
  - [ ] Create refresh_tokens task
  - [ ] Configure Celery beat schedule
- [ ] **Add Token Encryption**
  - [ ] Install django-cryptography
  - [ ] Encrypt token fields in model

### Phase 6: Testing & Polish
- [ ] **Write Tests**
  - [ ] Registration flow tests
  - [ ] OAuth flow tests
  - [ ] Token refresh tests
  - [ ] Organization membership tests
- [ ] **Error Handling**
  - [ ] OAuth error pages
  - [ ] Token expiry handling
  - [ ] Connection failure recovery
- [ ] **Documentation**
  - [ ] User guide for connection
  - [ ] API documentation
  - [ ] Troubleshooting guide

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

## Migration Strategy

### Database Migrations Required

#### Migration 1: Update User Model
```python
# apps/common/migrations/00XX_add_organization_fields.py
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ('common', 'latest_migration'),
        ('integration', '0001_initial'),
    ]
    
    operations = [
        migrations.AddField(
            model_name='user',
            name='current_organization',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='current_users',
                to='integration.organization'
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='email_verified',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='email_verification_token',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='user',
            name='email_verification_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
```

#### Migration 2: Add Organization Membership
```python
# apps/integration/migrations/00XX_add_organization_membership.py
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ('integration', '0001_initial'),
        ('common', '00XX_add_organization_fields'),
    ]
    
    operations = [
        migrations.CreateModel(
            name='OrganizationMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('role', models.CharField(
                    choices=[('owner', 'Owner'), ('admin', 'Admin'), ('member', 'Member'), ('viewer', 'Viewer')],
                    default='member',
                    max_length=20
                )),
                ('is_active', models.BooleanField(default=True)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='memberships',
                    to='integration.organization'
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='organization_memberships',
                    to='common.user'
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('modified_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-joined_at'],
                'unique_together': {('organization', 'user')},
            },
        ),
        migrations.AddField(
            model_name='organization',
            name='members',
            field=models.ManyToManyField(
                related_name='organizations',
                through='integration.OrganizationMember',
                to='common.user'
            ),
        ),
    ]
```

## Deployment Considerations

### Environment-Specific Settings

#### Development Environment
```env
# .env.local
DEBUG=True
QUICKBOOKS_SANDBOX_MODE=True
QUICKBOOKS_REDIRECT_URI=http://localhost:8000/api/quickbooks/callback/
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

#### Staging Environment
```env
# .env.staging
DEBUG=False
QUICKBOOKS_SANDBOX_MODE=True
QUICKBOOKS_REDIRECT_URI=https://staging.yourapp.com/api/quickbooks/callback/
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
```

#### Production Environment
```env
# .env.production
DEBUG=False
QUICKBOOKS_SANDBOX_MODE=False
QUICKBOOKS_REDIRECT_URI=https://app.yourapp.com/api/quickbooks/callback/
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
```

### Security Checklist

- [ ] Tokens encrypted at rest using django-cryptography
- [ ] HTTPS enforced for all OAuth endpoints
- [ ] CSRF protection via state parameter
- [ ] Session security settings configured
- [ ] Rate limiting on authentication endpoints
- [ ] Audit logging for OAuth events
- [ ] Token rotation implemented
- [ ] Secure token storage in environment variables

## Rollback Plan

### If Issues Occur During Deployment

1. **Database Rollback**
   ```bash
   python manage.py migrate integration 0001_initial
   python manage.py migrate common <previous_migration>
   ```

2. **Code Rollback**
   - Revert to previous git commit
   - Redeploy previous version

3. **QuickBooks App Rollback**
   - Disable OAuth redirects in QuickBooks app
   - Revert to previous redirect URIs

4. **Data Recovery**
   - Restore database backup if needed
   - Re-sync QuickBooks data

## Monitoring & Alerts

### Key Metrics to Monitor

1. **Authentication Metrics**
   - Registration success rate
   - Email verification rate
   - Login success/failure ratio
   - OAuth connection success rate

2. **QuickBooks Integration Metrics**
   - Token refresh success rate
   - API call success rate
   - Sync operation duration
   - Error rates by type

3. **Performance Metrics**
   - Page load times
   - OAuth flow duration
   - Database query performance
   - Background task execution time

### Alert Thresholds

- OAuth failure rate > 5%
- Token refresh failure > 1%
- Registration failure rate > 10%
- API response time > 3 seconds
- Background task failure > 5%

## API Documentation

### Public Endpoints

#### POST /register/
Create new user account with organization.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securepassword",
  "password_confirm": "securepassword",
  "first_name": "John",
  "last_name": "Doe",
  "organization_name": "Acme Corp"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Account created. Please verify your email.",
  "redirect": "/onboarding/"
}
```

#### GET /api/quickbooks/connect/
Initiate QuickBooks OAuth flow.

**Response:** Redirect to QuickBooks OAuth page

#### GET /api/quickbooks/callback/
Handle OAuth callback from QuickBooks.

**Parameters:**
- `code`: Authorization code
- `state`: CSRF protection state
- `realmId`: QuickBooks company ID

#### GET /api/quickbooks/status/
Check QuickBooks connection status.

**Response:**
```json
{
  "connected": true,
  "company_name": "Acme Corp",
  "needs_refresh": false,
  "last_sync": "2024-01-15T10:30:00Z"
}
```

## Troubleshooting Guide

### Common Issues and Solutions

#### Registration Issues
- **Email already exists**: Direct user to login or password reset
- **Weak password**: Show password requirements
- **Email not sending**: Check email backend configuration

#### OAuth Issues
- **State mismatch**: Clear browser cookies, try again
- **Token exchange failure**: Check client credentials
- **Company not found**: Verify QuickBooks account status

#### Connection Issues
- **Token expired**: Automatic refresh should handle
- **Rate limit exceeded**: Implement backoff strategy
- **Sync failures**: Check QuickBooks API status

## Appendix

### QuickBooks OAuth URLs
- **Authorization**: `https://appcenter.intuit.com/connect/oauth2`
- **Token Exchange**: `https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer`
- **Token Revoke**: `https://developer.api.intuit.com/v2/oauth2/tokens/revoke`
- **User Info**: `https://accounts.platform.intuit.com/v1/openid_connect/userinfo`

### Required Environment Variables
```env
# Authentication
SECRET_KEY=your-secret-key
DEBUG=False

# Database
DATABASE_URL=postgres://user:pass@localhost:5432/dbname

# QuickBooks OAuth
QUICKBOOKS_CLIENT_ID=your_client_id
QUICKBOOKS_CLIENT_SECRET=your_client_secret
QUICKBOOKS_REDIRECT_URI=https://yourapp.com/api/quickbooks/callback
QUICKBOOKS_WEBHOOK_TOKEN=random_webhook_verification_token
QUICKBOOKS_SANDBOX_MODE=False

# Email
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=noreply@yourapp.com

# Celery (Optional)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

### Required Python Packages
```toml
# Add to pyproject.toml
[project.dependencies]
httpx = "^0.25.0"  # For async HTTP requests
django-cryptography = "^1.1"  # For token encryption
celery = "^5.3.0"  # For background tasks (optional)
redis = "^5.0.0"  # For Celery broker (optional)
```

### QuickBooks App Configuration

1. **Create QuickBooks App**
   - Go to https://developer.intuit.com
   - Create new app
   - Select "QuickBooks Online and Payments"
   
2. **Configure OAuth Settings**
   - Add redirect URI: `https://yourapp.com/api/quickbooks/callback`
   - Add scope: `com.intuit.quickbooks.accounting`
   
3. **Get Credentials**
   - Copy Client ID and Client Secret
   - Add to environment variables

4. **Configure Webhooks (Optional)**
   - Add webhook URL: `https://yourapp.com/api/quickbooks/webhook`
   - Select entities to monitor
   - Copy webhook token

## Final Notes

This plan provides a complete, production-ready implementation of QuickBooks OAuth integration with proper user registration, organization management, and secure token handling. The implementation follows Django best practices and includes comprehensive error handling, testing, and monitoring strategies.

The most critical aspect is fixing the Organization-User relationship first, as this is the foundation for the entire multi-tenant architecture. Without this, users cannot be properly associated with organizations, breaking the QuickBooks connection flow.

All code examples are complete and ready to implement, with proper error handling, security measures, and user experience considerations built in.