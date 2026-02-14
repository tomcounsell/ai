# Deploying Cuttlefish to Render

This guide covers deploying the Cuttlefish Django application to Render.com with PostgreSQL database.

## Quick Deploy

The easiest way to deploy is using the included `render.yaml` configuration:

1. **Push to GitHub**: Ensure your code is pushed to the main branch
2. **Connect Render**: Go to [render.com](https://render.com) and connect your GitHub repository
3. **Auto-deploy**: Render will automatically detect `render.yaml` and create services
4. **Set secrets**: Add environment variables marked with `sync: false` in Render dashboard

## Configuration Files

### render.yaml

The `render.yaml` file defines the infrastructure:

- **PostgreSQL Database**: Free tier, Oregon region
- **Web Service**: Starter plan with health checks
- **Environment Variables**: Configured for production

### build.sh

The build script handles:
1. Installing uv package manager
2. Installing dependencies with `uv sync --all-extras`
3. Collecting static files
4. Running database migrations

## Environment Variables

### Environment Group: `cuttlefish`

Create an environment group named `cuttlefish` in the Render dashboard with these variables:

```bash
# Django Core
SECRET_KEY=<generate-random-secret-key>
DEBUG=False
ALLOWED_HOSTS=cuttlefish-production.onrender.com,ai.yuda.me

# QuickBooks OAuth (if using QuickBooks MCP)
QUICKBOOKS_CLIENT_ID=<your-quickbooks-client-id>
QUICKBOOKS_CLIENT_SECRET=<your-quickbooks-client-secret>
QUICKBOOKS_WEBHOOK_TOKEN=<your-webhook-token>
QUICKBOOKS_SANDBOX_MODE=False
```

### Auto-configured Variables

These are set automatically by `render.yaml`:

- `PORT=8000` - Web server port
- `DATABASE_URL` - PostgreSQL connection string (auto-generated)
- `DEPLOYMENT_TYPE=PRODUCTION` - Environment identifier

## Custom Domain Setup

To use `ai.yuda.me` domain:

1. **Add Custom Domain in Render**:
   - Go to your service settings
   - Click "Custom Domains"
   - Add `ai.yuda.me`

2. **Configure DNS**:
   ```
   CNAME  ai.yuda.me  cuttlefish-production.onrender.com
   ```

3. **SSL Certificate**: Render automatically provisions SSL certificates

## Deployment Process

### Initial Deployment

1. **Create Environment Group**:
   - Go to [render.com/dashboard](https://render.com/dashboard)
   - Click "Environment Groups" in left sidebar
   - Click "New Environment Group"
   - Name it `cuttlefish`
   - Add variables from "Environment Group: cuttlefish" section above

2. **Connect Repository**:
   ```bash
   # Ensure latest code is pushed
   git push origin main
   ```

3. **Create Service in Render**:
   - Go to [render.com/dashboard](https://render.com/dashboard)
   - Click "New +" → "Blueprint"
   - Select your repository
   - Render will parse `render.yaml`

4. **Deploy**:
   - Render automatically builds and deploys
   - Monitor build logs for any issues
   - Database and environment group are linked automatically

### Subsequent Deployments

Render automatically deploys when you push to the main branch:

```bash
git add .
git commit -m "Your changes"
git push origin main
# Render auto-deploys
```

## Monitoring

### Health Checks

The `/health/` endpoint returns "OK" for monitoring:

```bash
curl https://cuttlefish-production.onrender.com/health/
# Response: OK
```

Render automatically uses this for health checks (configured in `render.yaml`).

### Build Logs

Monitor deployment in Render dashboard:
- Go to your service
- Click "Logs" tab
- Filter by "Build" or "Runtime"

### Database Access

Connect to PostgreSQL:

1. Get credentials from Render dashboard
2. Use connection string from `DATABASE_URL`
3. Connect via psql or database GUI

## Service Configuration

### Web Service Specs

- **Plan**: Starter (can upgrade to Pro or higher)
- **Region**: Oregon
- **Runtime**: Python 3.11+
- **Start Command**: Gunicorn with WSGI
  ```
  gunicorn settings.wsgi:application --bind 0.0.0.0:$PORT --timeout 180 --keep-alive 5 --worker-connections 1000 --preload
  ```

### Background Worker Specs

- **Name**: `cuttlefish-worker`
- **Type**: Background Worker
- **Plan**: Starter
- **Region**: Oregon (same as web service)
- **Build Command**: Same as web service (`./build.sh`)
- **Start Command**: `python manage.py db_worker`
- **Environment**: Same env group as web service (`cuttlefish`)

The worker uses Django 6.0's native `@task` framework with `django-tasks-db` (`DatabaseBackend`). It polls PostgreSQL for enqueued tasks and executes them. The worker shares the same database and environment as the web service.

Tasks are defined with `@task` decorator and enqueued with `.enqueue()`. In dev/test, the `ImmediateBackend` runs tasks inline — no worker needed.

### Database Specs

- **Plan**: Free (can upgrade to paid plans)
- **Region**: Oregon (same as web service for low latency)
- **Database**: `cuttlefish`
- **User**: `cuttlefish_user`

## MCP Server Deployment

The Creative Juices MCP and QuickBooks MCP servers are accessible via Django:

- **Creative Juices Landing**: https://ai.yuda.me/mcp/creative-juices
- **Creative Juices Manifest**: https://ai.yuda.me/mcp/creative-juices/manifest.json
- **QuickBooks OAuth**: https://ai.yuda.me/api/quickbooks/connect/

These are served by Django views and work automatically once deployed.

## Scaling

### Upgrading Plans

To handle more traffic:

1. **Upgrade Web Service**:
   - Starter → Pro → Pro Max
   - Increases CPU/memory
   - Enables auto-scaling

2. **Upgrade Database**:
   - Free → Starter → Standard → Pro
   - Increases storage and performance

### Adding Redis (Optional)

To enable Redis for caching/sessions:

1. **Uncomment in render.yaml**:
   ```yaml
   services:
     - type: keyvalue
       name: redis-production
       region: oregon
       plan: starter
   ```

2. **Uncomment Redis env var**:
   ```yaml
   - key: REDIS_URL
     fromService:
       type: keyvalue
       name: redis-production
       property: connectionString
   ```

3. **Update Django settings** to use Redis for cache/sessions

## Troubleshooting

### Build Fails

**Issue**: `uv sync` fails
- **Solution**: Check `pyproject.toml` for incompatible dependencies
- **Debug**: Review build logs in Render dashboard

**Issue**: Static files not collected
- **Solution**: Ensure `STATIC_ROOT` is configured in settings
- **Debug**: Check `collectstatic` output in build logs

### Runtime Issues

**Issue**: 502 Bad Gateway
- **Solution**: Check application logs for startup errors
- **Debug**: Verify `gunicorn` command is correct

**Issue**: Database connection fails
- **Solution**: Verify `DATABASE_URL` is set correctly
- **Debug**: Check database is in same region as web service

**Issue**: Health check fails
- **Solution**: Ensure `/health/` endpoint responds with 200 OK
- **Debug**: Test endpoint directly: `curl https://your-app.onrender.com/health/`

### Environment Variables

**Issue**: Settings not loading
- **Solution**: Verify all required env vars are set
- **Check**: `DEPLOYMENT_TYPE`, `SECRET_KEY`, `DATABASE_URL`

**Issue**: QuickBooks OAuth fails
- **Solution**: Add QuickBooks credentials to Render
- **Verify**: Redirect URI matches Render URL

## Manual Deployment Commands

For advanced deployments, use Render CLI:

```bash
# Install Render CLI
brew install render

# Login
render login

# Deploy from CLI
render deploy

# View logs
render logs -f

# Run database migrations manually
render run python manage.py migrate
```

## Security Considerations

- **SECRET_KEY**: Auto-generated by Render, never commit to repo
- **DEBUG**: Set to False in production (configured in render.yaml)
- **ALLOWED_HOSTS**: Restricted to Render and custom domain
- **Database**: Private, only accessible from Render services
- **SSL**: Automatically configured by Render

## Cost Estimates

### Free Tier
- PostgreSQL: Free (max 1GB storage)
- Web Service: Not available (minimum Starter plan)

### Minimal Production ($14/month)
- PostgreSQL: Free
- Web Service: Starter ($7/month)
- Background Worker: Starter ($7/month)

### Recommended Production ($35/month)
- PostgreSQL: Starter ($7/month)
- Web Service: Pro ($21/month)
- Background Worker: Starter ($7/month)
- Auto-scaling enabled on web

### With Redis ($42/month)
- PostgreSQL: Starter ($7/month)
- Web Service: Pro ($21/month)
- Background Worker: Starter ($7/month)
- Redis: Starter ($7/month)

## Next Steps

After deployment:

1. **Verify Health**: Check https://your-app.onrender.com/health/
2. **Test MCP Endpoints**: Visit MCP landing pages
3. **Configure OAuth**: Set QuickBooks redirect URI
4. **Add Custom Domain**: Configure DNS for ai.yuda.me
5. **Monitor**: Set up alerts in Render dashboard
6. **Document URLs**: Update team documentation with production URLs

## Support

- **Render Docs**: https://render.com/docs
- **Django Deployment**: https://docs.djangoproject.com/en/stable/howto/deployment/
- **Project Issues**: https://github.com/tomcounsell/cuttlefish/issues
