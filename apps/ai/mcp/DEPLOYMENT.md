# Deploying Creative Juices MCP via Django

The Creative Juices MCP is deployed as part of the Cuttlefish Django application at **https://ai.yuda.me/mcp/creative-juices**.

## Deployment Architecture

This is a **Django-hosted deployment**, not static file hosting. The MCP landing page, manifest, and README are served through Django views.

### URLs

- **Landing Page**: https://ai.yuda.me/mcp/creative-juices
- **Manifest**: https://ai.yuda.me/mcp/creative-juices/manifest.json
- **README**: https://ai.yuda.me/mcp/creative-juices/README.md

### Implementation

**Views** (`apps/ai/views/mcp_views.py`):
- `CreativeJuicesLandingView` - Serves the HTML landing page
- `CreativeJuicesManifestView` - Serves manifest.json with CORS headers
- `CreativeJuicesReadmeView` - Serves README.md as markdown

**URL Routing** (`apps/ai/urls.py`):
```python
path("mcp/creative-juices/", CreativeJuicesLandingView.as_view(), name="mcp-creative-juices"),
path("mcp/creative-juices/manifest.json", CreativeJuicesManifestView.as_view(), name="mcp-creative-juices-manifest"),
path("mcp/creative-juices/README.md", CreativeJuicesReadmeView.as_view(), name="mcp-creative-juices-readme"),
```

**Source Files** (served dynamically):
- `apps/ai/mcp/creative_juices_web.html` - Landing page HTML
- `apps/ai/mcp/creative_juices_manifest.json` - MCP manifest
- `apps/ai/mcp/CREATIVE_JUICES_README.md` - Installation guide

## Django Deployment Setup

### Prerequisites

- Python 3.11+
- PostgreSQL database
- uv package manager
- Domain configured: ai.yuda.me

### Initial Setup

```bash
# Clone repository
git clone https://github.com/tomcounsell/cuttlefish.git
cd cuttlefish

# Install dependencies
uv sync --all-extras

# Configure environment
cp .env.example .env.local
# Edit .env.local with your settings

# Setup database
createdb cuttlefish
uv run python manage.py migrate

# Test locally
uv run python manage.py runserver
# Visit http://localhost:8000/ai/mcp/creative-juices
```

### Production Deployment

#### Option 1: Gunicorn + Nginx

**1. Install production server**
```bash
uv add gunicorn
```

**2. Run Gunicorn**
```bash
uv run gunicorn settings.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --timeout 120
```

**3. Nginx configuration**
```nginx
server {
    listen 80;
    server_name ai.yuda.me;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /var/www/cuttlefish/staticfiles/;
    }

    location /media/ {
        alias /var/www/cuttlefish/media/;
    }
}
```

**4. SSL with Let's Encrypt**
```bash
sudo certbot --nginx -d ai.yuda.me
```

#### Option 2: Platform-as-a-Service

**Heroku**
```bash
# Create Procfile
echo "web: gunicorn settings.wsgi:application" > Procfile

# Deploy
heroku create cuttlefish-ai
heroku addons:create heroku-postgresql:hobby-dev
git push heroku main
heroku run python manage.py migrate
```

**Railway/Render/Fly.io**
- Connect GitHub repository
- Set environment variables from `.env.example`
- Configure build command: `uv sync --all-extras`
- Configure start command: `uv run gunicorn settings.wsgi:application`
- Set custom domain: ai.yuda.me

#### Option 3: Docker Deployment

**Dockerfile** (create if needed):
```dockerfile
FROM python:3.11-slim

# Install uv
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Install dependencies
RUN uv sync --all-extras

# Collect static files
RUN uv run python manage.py collectstatic --noinput

# Run gunicorn
CMD ["uv", "run", "gunicorn", "settings.wsgi:application", "--bind", "0.0.0.0:8000"]
```

**Deploy with Docker Compose**:
```yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://user:pass@db:5432/cuttlefish
      - DEPLOYMENT_TYPE=PRODUCTION
    depends_on:
      - db

  db:
    image: postgres:15
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=cuttlefish
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass

volumes:
  postgres_data:
```

## Environment Configuration

Required in `.env.local` or platform environment variables:

```bash
# Database
DATABASE_URL=postgres://user:pass@localhost:5432/cuttlefish

# Django
SECRET_KEY=your-secret-key-here
DEBUG=False
DEPLOYMENT_TYPE=PRODUCTION
ALLOWED_HOSTS=ai.yuda.me

# Static files (for production)
STATIC_ROOT=/var/www/cuttlefish/staticfiles
MEDIA_ROOT=/var/www/cuttlefish/media
```

## CORS Configuration

The manifest.json view automatically includes CORS headers for cross-origin access:

```python
response["Access-Control-Allow-Origin"] = "*"
response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
response["Access-Control-Allow-Headers"] = "Content-Type"
```

No additional CORS configuration needed.

## DNS Configuration

Point your domain to the server:

```dns
# A Record
ai.yuda.me.  A      YOUR.SERVER.IP.ADDRESS

# Or CNAME (for PaaS)
ai.yuda.me.  CNAME  your-app.herokuapp.com.
```

## Testing Deployment

Once deployed, verify:

```bash
# 1. Landing page loads
curl https://ai.yuda.me/mcp/creative-juices

# 2. Manifest accessible with CORS
curl -I https://ai.yuda.me/mcp/creative-juices/manifest.json

# 3. README accessible
curl https://ai.yuda.me/mcp/creative-juices/README.md

# 4. SSL valid
curl -I https://ai.yuda.me/mcp/creative-juices | grep "200 OK"
```

Browser tests:
1. Visit https://ai.yuda.me/mcp/creative-juices - should show landing page
2. Check browser dev tools for CORS headers on manifest.json
3. Verify SSL certificate is valid (no warnings)

## Updating the MCP

When updating the MCP content:

1. **Edit source files** in `apps/ai/mcp/`:
   - `creative_juices_web.html` - Landing page
   - `creative_juices_manifest.json` - Manifest (update version)
   - `CREATIVE_JUICES_README.md` - Documentation

2. **Commit changes**:
   ```bash
   git add apps/ai/mcp/
   git commit -m "Update Creative Juices MCP content"
   git push
   ```

3. **Deploy to production**:
   - PaaS: Automatic on push (if configured)
   - Manual: Pull changes and restart Django
   ```bash
   git pull
   sudo systemctl restart gunicorn  # or your process manager
   ```

4. **Verify changes**: Visit https://ai.yuda.me/mcp/creative-juices

## Monitoring & Maintenance

### Health Checks

Add to `urls.py` for monitoring:
```python
path("health/", lambda r: HttpResponse("OK"), name="health"),
```

Monitor: https://ai.yuda.me/health/

### Logging

Django logs are configured in `settings/base.py`. Monitor for:
- 404s on MCP URLs (missing assets)
- 500s (server errors)
- MCP endpoint access patterns

### Performance

- **Django caching**: Consider caching views for static content
- **CDN**: Optional for manifest.json (Cloudflare, etc.)
- **Monitoring**: Use Sentry, New Relic, or DataDog for Django monitoring

## Security Notes

- **Read-only views**: MCP views only serve static files, no write operations
- **No authentication**: MCP content is public by design
- **CORS enabled**: Necessary for manifest.json access from MCP registries
- **No sensitive data**: MCP files contain only public information

## Future Enhancements

### Analytics (Optional)

Add privacy-friendly analytics to landing page:
```html
<!-- In creative_juices_web.html -->
<script defer data-domain="ai.yuda.me" src="https://plausible.io/js/script.js"></script>
```

### API Endpoint (Future)

Consider adding a demo API endpoint:
```python
# In apps/ai/views/mcp_views.py
class CreativeJuicesApiView(View):
    def get(self, request):
        # Generate random spark without full MCP server
        from apps.ai.mcp.creative_juices_words import VERBS, NOUNS
        import random

        verb = random.choice(VERBS["inspiring"])
        noun = random.choice(NOUNS["inspiring"])

        return JsonResponse({
            "spark": f"{verb}-{noun}",
            "timestamp": timezone.now().isoformat()
        })
```

### MCP Registry Submission

Once stable, submit to MCP registries with:
- Manifest URL: https://ai.yuda.me/mcp/creative-juices/manifest.json
- Landing page: https://ai.yuda.me/mcp/creative-juices
- Documentation: Link to GitHub README

## Troubleshooting

**Issue**: 404 on MCP URLs
- Check Django routing is active: `ai/` prefix in main `settings/urls.py`
- Verify views are imported in `apps/ai/views/__init__.py`

**Issue**: CORS errors on manifest.json
- Confirm `Access-Control-Allow-Origin: *` header is set
- Check browser console for specific CORS error

**Issue**: Static files not loading
- Run `python manage.py collectstatic`
- Check `STATIC_ROOT` and nginx configuration

**Issue**: 500 errors
- Check Django logs: `tail -f /var/log/gunicorn/error.log`
- Verify all environment variables are set
- Ensure database is accessible
