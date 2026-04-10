from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.integration.stripe.webhook import handle_stripe_webhook


@csrf_exempt
@require_POST
def stripe_webhook_view(request):
    """
    Receive and process Stripe webhook events.

    This view is CSRF-exempt because Stripe sends webhook requests from
    outside the browser session. Signature verification is handled by
    handle_stripe_webhook via the Stripe-Signature header.
    """
    payload = request.body
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    result = handle_stripe_webhook(payload, signature)

    if not result.get("success"):
        return JsonResponse(result, status=400)

    return JsonResponse(result, status=200)
