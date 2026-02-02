# Code Execution Module - Implementation Roadmap

**Purpose**: Step-by-step guide from proof-of-concept to production-scale deployment
**Audience**: Future developers implementing safe code execution
**Status**: Documentation complete, ready for implementation

---

## Current State: Proof of Concept (Complete ✅)

**What's Done:**
- ✅ Complete architecture with 6 security layers
- ✅ Comprehensive documentation (4,000+ lines)
- ✅ 30+ security tests with adversarial cases
- ✅ Pluggable sandbox design (Strategy pattern)
- ✅ RestrictedPythonSandbox (dev/testing only)
- ✅ Validation layers (syntax, AST, output)
- ✅ Django models for tracking
- ✅ Real-world examples and integration patterns
- ✅ Production sandbox research and comparison

**What's NOT Production-Ready:**
- ❌ RestrictedPythonSandbox (pure Python, can be bypassed)
- ❌ No OS-level isolation
- ❌ No rate limiting implementation
- ❌ No Celery integration
- ❌ No monitoring/alerting
- ❌ No production deployment guide

---

## Phase 1: MVP Deployment (Weeks 1-4)

**Goal**: Get to production with E2B in 1 month

### Week 1: E2B Integration

**Task 1.1: Set up E2B account**
- [ ] Sign up at https://e2b.dev
- [ ] Get API key
- [ ] Add to `.env.local`: `E2B_API_KEY=sk_...`
- [ ] Test basic connection

**Task 1.2: Add E2B dependency**
```bash
uv add e2b
```

**Task 1.3: Implement E2BSandbox**
- [ ] Copy implementation from `PRODUCTION_SANDBOX_COMPARISON.md`
- [ ] Create `apps/ai/code_execution/sandboxes/e2b_sandbox.py`
- [ ] Add to `sandboxes/__init__.py` exports
- [ ] Add `E2B_API_KEY` to `settings/third_party.py`

**Task 1.4: Update tests**
- [ ] Create `tests/test_e2b_sandbox.py`
- [ ] Run existing security tests with E2BSandbox
- [ ] Verify all tests pass (or skip if no API key)

**Success Criteria:**
- ✅ E2BSandbox implements BaseSandbox interface
- ✅ All security tests pass with E2BSandbox
- ✅ Can execute basic code in E2B sandbox
- ✅ Proper error handling and cleanup

**Estimated Time**: 2-3 days

---

### Week 2: Django Integration

**Task 2.1: Add execution tracking**
- [ ] Run migrations for CodeExecution model
- [ ] Run migrations for ExecutionQuota model
- [ ] Test model creation and queries

**Task 2.2: Create API endpoint**
```python
# apps/api/views/code_execution.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from apps.ai.code_execution import CodeExecutor
from apps.ai.code_execution.sandboxes import E2BSandbox

@api_view(['POST'])
def execute_code(request):
    """Execute code in E2B sandbox."""
    code = request.data.get('code')
    context = request.data.get('context', {})

    executor = CodeExecutor(
        user_id=request.user.id,
        sandbox_type=E2BSandbox,
    )

    result = executor.execute(code, context=context)

    # Save to database
    from apps.ai.code_execution.models import CodeExecution
    execution = CodeExecution.create_from_result(
        user=request.user,
        code=code,
        result=result,
        context=context,
    )
    execution.save()

    return Response(result.to_dict())
```

**Task 2.3: Add admin interface**
```python
# apps/ai/admin.py
from django.contrib import admin
from apps.ai.code_execution.models import CodeExecution, ExecutionQuota

@admin.register(CodeExecution)
class CodeExecutionAdmin(admin.ModelAdmin):
    list_display = ['user', 'success', 'created_at', 'execution_time_seconds']
    list_filter = ['success', 'created_at', 'sandbox_type']
    search_fields = ['user__username', 'code', 'error_message']
    readonly_fields = ['created_at', 'modified_at']

@admin.register(ExecutionQuota)
class ExecutionQuotaAdmin(admin.ModelAdmin):
    list_display = ['user', 'executions_used', 'executions_limit', 'period_end']
```

**Task 2.4: Test integration**
- [ ] Test API endpoint with Postman/curl
- [ ] Verify executions appear in admin
- [ ] Test error handling
- [ ] Test quota enforcement

**Success Criteria:**
- ✅ API endpoint works with E2B
- ✅ Executions stored in database
- ✅ Admin interface functional
- ✅ Error responses are useful

**Estimated Time**: 3-4 days

---

### Week 3: Rate Limiting & Monitoring

**Task 3.1: Implement rate limiting**
```python
# apps/api/views/code_execution.py
from apps.ai.code_execution.models import ExecutionQuota

@api_view(['POST'])
def execute_code(request):
    # Check quota
    quota = ExecutionQuota.get_or_create_for_user(request.user)

    if not quota.can_execute():
        return Response({
            'error': 'Quota exceeded',
            'details': {
                'executions_used': quota.executions_used,
                'executions_limit': quota.executions_limit,
                'reset_at': quota.period_end,
            }
        }, status=429)

    # Execute code...
    result = executor.execute(code, context=context)

    # Record against quota
    quota.record_execution(result)

    return Response(result.to_dict())
```

**Task 3.2: Set up logging**
```python
# settings/base.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'code_execution': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/code_execution.log',
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
        },
    },
    'loggers': {
        'apps.ai.code_execution': {
            'handlers': ['code_execution', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

**Task 3.3: Add monitoring**
- [ ] Set up error tracking (Sentry)
- [ ] Add execution time metrics
- [ ] Monitor E2B API errors
- [ ] Set up alerts for quota limits

**Success Criteria:**
- ✅ Users cannot exceed quota
- ✅ All executions logged
- ✅ Errors sent to Sentry
- ✅ Basic metrics tracked

**Estimated Time**: 2-3 days

---

### Week 4: Testing & Documentation

**Task 4.1: End-to-end testing**
- [ ] Test complete user flow
- [ ] Test with LLM-generated code
- [ ] Test error scenarios
- [ ] Test quota enforcement
- [ ] Load testing (100 concurrent executions)

**Task 4.2: Write user documentation**
- [ ] API documentation
- [ ] Rate limit information
- [ ] Error handling guide
- [ ] Example integrations

**Task 4.3: Security review**
- [ ] Verify all validation layers enabled
- [ ] Review E2B configuration
- [ ] Test with adversarial inputs
- [ ] Document security policies

**Task 4.4: Deploy to staging**
- [ ] Deploy with E2B enabled
- [ ] Verify E2B API key in production settings
- [ ] Test from staging environment
- [ ] Monitor for 48 hours

**Success Criteria:**
- ✅ All tests passing
- ✅ Documentation complete
- ✅ Staging deployment successful
- ✅ No security issues found

**Estimated Time**: 4-5 days

---

## Phase 2: Optimization (Months 2-3)

**Goal**: Reduce costs and improve performance

### Month 2: Cost Optimization

**Task: Add validation-based routing**
```python
class SmartExecutor:
    """Routes execution based on code complexity."""

    def execute(self, code: str):
        # Validate first
        violations = self.ast_validator.validate(code)

        # Simple calculations: use RestrictedPython (free)
        if self._is_simple_calculation(code) and not violations:
            return RestrictedPythonSandbox().execute(code)

        # Everything else: use E2B (secure)
        return E2BSandbox().execute(code)

    def _is_simple_calculation(self, code: str) -> bool:
        """Check if code is just arithmetic/math."""
        tree = ast.parse(code)
        # No imports, no function calls, just expressions
        return all(
            isinstance(node, (ast.Expr, ast.BinOp, ast.Num, ast.Name))
            for node in ast.walk(tree)
        )
```

**Expected Savings**: 20-40% reduction in E2B costs

---

### Month 3: Performance Optimization

**Task 3.1: Add result caching**
```python
from django.core.cache import cache
import hashlib

class CachingExecutor:
    def execute(self, code: str):
        # Check cache
        cache_key = hashlib.sha256(code.encode()).hexdigest()
        cached = cache.get(f"execution:{cache_key}")

        if cached:
            return cached

        # Execute and cache
        result = self.executor.execute(code)

        if result.success:
            cache.set(f"execution:{cache_key}", result, timeout=3600)

        return result
```

**Task 3.2: Sandbox pooling**
```python
class SandboxPool:
    """Maintain pool of warm sandboxes."""

    def __init__(self, size=5):
        self.pool = Queue()
        for _ in range(size):
            self.pool.put(E2BSandbox())

    def get(self):
        return self.pool.get(timeout=5)

    def release(self, sandbox):
        self.pool.put(sandbox)
```

**Expected Improvements**: 50% faster response time

---

## Phase 3: Scale (Months 4-6)

**Goal**: Support 10,000+ executions/day

### Celery Integration

**Task: Async execution**
```python
# apps/ai/code_execution/tasks.py
from celery import shared_task

@shared_task
def execute_code_async(code: str, user_id: int, context: dict):
    """Execute code asynchronously."""
    executor = CodeExecutor(
        user_id=user_id,
        sandbox_type=E2BSandbox,
    )

    result = executor.execute(code, context=context)

    # Save to database
    execution = CodeExecution.create_from_result(
        user=User.objects.get(id=user_id),
        code=code,
        result=result,
        context=context,
    )
    execution.save()

    return result.to_dict()


# Usage in views
from .tasks import execute_code_async

def execute_code_endpoint(request):
    task = execute_code_async.delay(
        code=request.data['code'],
        user_id=request.user.id,
        context=request.data.get('context', {}),
    )

    return Response({
        'task_id': task.id,
        'status': 'pending',
        'status_url': f'/api/tasks/{task.id}/status/'
    })
```

### Cost Evaluation

At 10,000+ exec/day, evaluate self-hosted:

**Decision Point:**
- E2B cost: ~$500-1000/month
- gVisor cost: $200/month + 2 weeks engineering
- **Break-even**: ~2 months

**If cost justifies, implement gVisor:**
See `PRODUCTION_SANDBOX_COMPARISON.md` for implementation guide

---

## Phase 4: Enterprise Scale (Months 7-12)

**Goal**: Support 100,000+ executions/day

### Infrastructure Evolution

**Month 7-8: Implement gVisor**
- Set up Docker with runsc runtime
- Implement GVisorSandbox class
- Migrate 10% of traffic
- Compare performance and costs

**Month 9-10: Full migration to gVisor**
- Migrate remaining traffic
- Keep E2B as fallback
- Monitor savings

**Month 11-12: Advanced features**
- Real-time output streaming
- Multi-language support
- Advanced caching strategies
- Custom resource policies

### At 100k+ exec/day: Consider Firecracker

Only if you have:
- Dedicated infrastructure team
- 2-3 months for implementation
- Need for maximum isolation

See `PRODUCTION_SANDBOX_COMPARISON.md` for Firecracker guidance.

---

## Decision Checkpoints

### Checkpoint 1: After Month 1
**Question**: Is E2B working well?
- ✅ Yes → Continue to Phase 2 (optimization)
- ❌ No → Debug issues, get E2B support

### Checkpoint 2: After Month 3
**Question**: Are costs under control?
- ✅ < $500/month → Stay with E2B
- ⚠️ $500-1000/month → Add optimizations
- ❌ > $1000/month → Consider self-hosted

### Checkpoint 3: After Month 6
**Question**: What's our execution volume?
- < 5k/day → Stay with E2B
- 5k-20k/day → Consider gVisor
- > 20k/day → Implement gVisor

### Checkpoint 4: After Year 1
**Question**: What's our scale?
- < 50k/day → gVisor is sufficient
- 50k-100k/day → Optimize gVisor
- > 100k/day → Evaluate Firecracker

---

## Testing Strategy Throughout

### Every Phase

**Unit Tests:**
```bash
pytest apps/ai/code_execution/tests/test_security.py -v
```

**Integration Tests:**
```bash
pytest apps/ai/code_execution/tests/test_executor.py -v
```

**Security Tests:**
```python
# Run adversarial test suite
pytest apps/ai/code_execution/tests/test_security.py::TestEscapeAttempts -v
```

**Load Tests:**
```bash
# Using locust or similar
locust -f tests/load_test.py --users 100 --spawn-rate 10
```

---

## Monitoring Metrics

### Track Throughout All Phases

**Performance Metrics:**
- Execution time (p50, p95, p99)
- Validation time
- Queue wait time (if using Celery)
- Sandbox startup time

**Cost Metrics:**
- Total executions per day
- E2B API costs
- Infrastructure costs
- Cost per execution

**Security Metrics:**
- Validation violations per day
- High-severity security violations
- Output redactions per day
- Failed executions

**User Metrics:**
- Executions per user
- Quota exceeded events
- Error rate per user
- Average execution time per user

---

## Rollback Plan

### If E2B Has Issues

**Immediate (< 1 hour):**
1. Disable code execution feature
2. Show maintenance message to users
3. Investigate E2B status page

**Short-term (< 1 day):**
1. Contact E2B support
2. Check API key and credentials
3. Review error logs
4. Test with simple code

**Medium-term (< 1 week):**
1. Implement fallback to RestrictedPython (limited access)
2. Add feature flag for E2B vs RestrictedPython
3. Communicate timeline to users

**Long-term (< 1 month):**
1. If E2B unreliable, implement gVisor
2. Document incident
3. Update architecture decision records

---

## Success Criteria

### Phase 1 (MVP)
- ✅ Can execute code safely in production
- ✅ Rate limiting prevents abuse
- ✅ All security tests passing
- ✅ < 5% error rate
- ✅ < 2s average response time

### Phase 2 (Optimization)
- ✅ 20-40% cost reduction via smart routing
- ✅ < 1s average response time
- ✅ Cache hit rate > 30%

### Phase 3 (Scale)
- ✅ Handles 10,000+ executions/day
- ✅ Async execution working
- ✅ Cost per execution < $0.05

### Phase 4 (Enterprise)
- ✅ Handles 100,000+ executions/day
- ✅ Multiple sandbox backends
- ✅ Cost per execution < $0.01
- ✅ 99.9% uptime

---

## Resources

**Documentation:**
- `README.md` - Complete API reference
- `QUICKSTART.md` - Get started in 5 minutes
- `EXAMPLES.md` - Real-world usage patterns
- `PRODUCTION_SANDBOX_COMPARISON.md` - Choose the right sandbox
- `IMPLEMENTATION_SUMMARY.md` - Architecture deep dive

**External Resources:**
- [E2B Documentation](https://e2b.dev/docs)
- [gVisor Guide](https://gvisor.dev/docs/)
- [Firecracker](https://firecracker-microvm.github.io/)
- [OWASP Secure Coding](https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/)

**Support:**
- Architecture questions: Review docs
- Security concerns: Run test suite
- Performance issues: Check monitoring
- E2B issues: Contact E2B support

---

## Final Checklist: Production Deployment

Before deploying to production:

### Security
- [ ] Using E2B (or gVisor/Firecracker)
- [ ] All validation layers enabled
- [ ] Rate limiting configured
- [ ] Output redaction enabled
- [ ] Security tests passing
- [ ] Tested with adversarial inputs

### Performance
- [ ] Average response time < 2s
- [ ] Error rate < 5%
- [ ] Load tested to expected volume
- [ ] Caching configured (if Phase 2+)

### Monitoring
- [ ] Logging configured
- [ ] Error tracking (Sentry) enabled
- [ ] Metrics dashboard set up
- [ ] Alerts configured
- [ ] On-call rotation established

### Documentation
- [ ] API docs published
- [ ] User guide available
- [ ] Error messages documented
- [ ] Security policies documented

### Operations
- [ ] Rollback plan documented
- [ ] Incident response plan ready
- [ ] Team trained on system
- [ ] E2B support contact available

---

## Conclusion

This roadmap provides a clear path from proof-of-concept to production scale:

1. **Week 1-4**: Deploy with E2B (fastest path to production)
2. **Month 2-3**: Optimize costs and performance
3. **Month 4-6**: Scale to 10k+ executions/day
4. **Month 7-12**: Evaluate self-hosted if justified

The architecture is proven, the documentation is complete, and the path is clear. Future developers have everything needed to build a secure, scalable code execution system.

**Next step**: Implement E2BSandbox class and deploy Phase 1.

---

*Good luck! You're building something powerful and doing it securely.* 🚀
