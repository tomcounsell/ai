# API App

## Purpose

The API app provides a RESTful API interface for the application, allowing programmatic access to data and functionality. It serves as the backend for mobile applications, third-party integrations, and client-side JavaScript applications.

## Features

### API Views

- **User Views**: Endpoints for user authentication and profile management
- **Image Views**: Endpoints for image upload and retrieval

### Serializers

- **User Serializer**: Converts User models to/from JSON

### Testing

- **APITestCase**: Base test case for API endpoints with assertion helpers

## Technical Approach

The API app is built on Django REST Framework and follows these principles:

1. **RESTful Design**: Resources are represented as URLs
2. **Authentication**: Multiple authentication methods (Session, Token, API Key)
3. **Permissions**: Fine-grained permission control
4. **Serialization**: Conversion between Python objects and API formats
5. **Documentation**: API documentation with OpenAPI/Swagger

## API Documentation

API documentation is provided using drf-yasg (Django REST Framework Yet Another Swagger Generator) with both Swagger UI and ReDoc interfaces:

- **Swagger UI**: Interactive API documentation at `/api/swagger/`
- **ReDoc**: Alternative documentation interface at `/api/redoc/`
- **OpenAPI Schema**: Raw JSON schema at `/api/swagger/?format=json`

These interfaces provide:
- Complete endpoint listing with request/response formats
- Interactive testing capability
- Authentication support
- Model schema definitions

## Endpoints

- `/api/users/`: User management
- `/api/user-api-keys/`: User API key management
- `/api/team-api-keys/`: Team API key management
- `/api/todos/`: Todo item management
- `/api/images/`: Image uploading and management

## Authentication

The API supports multiple authentication methods:

### Session Authentication
Browser-based authentication using Django's session framework. This is the default method for users accessing the API through a web browser.

### API Key Authentication
For programmatic access to the API from scripts, external services, or applications.

#### User API Keys
- Associated with a specific user
- Full access to all resources the user has permission to access
- Created and managed by the user via the `/api/user-api-keys/` endpoint

#### Team API Keys
- Associated with a specific team
- Access to team resources
- Created and managed by team owners and admins via the `/api/team-api-keys/` endpoint

#### Using API Keys
To authenticate using an API key, include the key in the HTTP header:

```http
X-API-KEY: {your_api_key}
```

## Development Guidelines

- All API endpoints should have thorough tests
- Use standard REST conventions (GET, POST, PUT, DELETE)
- Implement proper validation and error handling
- Document all endpoints with docstrings and OpenAPI
- Use custom permissions where needed
- Version the API when making breaking changes
