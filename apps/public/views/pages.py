"""
Views for the example pages (landing, pricing, blog).

These views demonstrate best practices for rendering different types of pages,
including authentication-optional pages and content-driven pages.
"""

from django.http import Http404
from django.utils import timezone

from apps.public.views.helpers.main_content_view import MainContentView


class LandingView(MainContentView):
    """Landing page for the site."""

    template_name = "pages/landing.html"
    active_nav = "home"  # Use home as the active nav

    def get(self, request, *args, **kwargs):
        """Get method to load landing page context."""
        self.context.update(self.get_context_data())
        return self.render(request)

    def get_context_data(self, **kwargs):
        """Add additional context for the landing page."""
        context = {}

        # Features list for landing page
        context["features"] = [
            {
                "title": "Powerful Tools",
                "description": "Access a suite of powerful tools to boost your productivity.",
                "icon": "tools",
            },
            {
                "title": "Secure Platform",
                "description": "Your data is safe with our enterprise-grade security.",
                "icon": "shield-alt",
            },
            {
                "title": "Seamless Integration",
                "description": "Integrate with your favorite tools and services.",
                "icon": "plug",
            },
        ]

        # Steps for "How it works" section
        context["steps"] = [
            {
                "number": 1,
                "title": "Create an Account",
                "description": "Sign up for free and get started in minutes.",
            },
            {
                "number": 2,
                "title": "Configure Your Settings",
                "description": "Customize the platform to match your needs.",
            },
            {
                "number": 3,
                "title": "Start Building",
                "description": "Use our tools to create amazing things.",
            },
        ]

        return context


class PricingView(MainContentView):
    """Pricing page for the site."""

    template_name = "pages/pricing.html"
    active_nav = "pricing"  # Add this to active_navigation context processor

    def get(self, request, *args, **kwargs):
        """Get method to load pricing plans into context."""
        self.context.update(self.get_context_data())
        return self.render(request)

    def get_context_data(self, **kwargs):
        """Add pricing plans to the context."""
        context = {}

        # Pricing plans
        context["plans"] = [
            {
                "name": "Free",
                "price": "$0",
                "period": "forever",
                "description": "Perfect for getting started and exploring the platform.",
                "features": [
                    "Basic features",
                    "Up to 3 projects",
                    "Community support",
                    "1 GB storage",
                ],
                "highlighted": False,
                "cta_text": "Get Started",
                "cta_class": "bg-slate-600 hover:bg-slate-700",
            },
            {
                "name": "Premium",
                "price": "$12",
                "period": "per month",
                "description": "For professionals who need more power and features.",
                "features": [
                    "All Free features",
                    "Unlimited projects",
                    "Priority support",
                    "10 GB storage",
                    "Advanced analytics",
                    "Team collaboration",
                ],
                "highlighted": True,
                "cta_text": "Start Free Trial",
                "cta_class": "bg-slate-900 hover:bg-slate-800",
            },
            {
                "name": "Enterprise",
                "price": "$49",
                "period": "per month",
                "description": "For teams and organizations with advanced needs.",
                "features": [
                    "All Premium features",
                    "Dedicated support",
                    "100 GB storage",
                    "Custom integrations",
                    "Advanced security",
                    "SLA guarantees",
                ],
                "highlighted": False,
                "cta_text": "Contact Sales",
                "cta_class": "bg-slate-600 hover:bg-slate-700",
            },
        ]

        # FAQs about pricing
        context["faqs"] = [
            {
                "question": "Can I upgrade or downgrade my plan at any time?",
                "answer": "Yes, you can change your plan at any time. When upgrading, you'll be charged the prorated difference. When downgrading, the new rate will apply at the start of your next billing cycle.",
            },
            {
                "question": "Do you offer discounts for nonprofits or educational institutions?",
                "answer": "Yes! We offer special pricing for qualified nonprofits, educational institutions, and open source projects. Please contact our sales team for details.",
            },
            {
                "question": "What payment methods do you accept?",
                "answer": "We accept all major credit cards (Visa, Mastercard, American Express, Discover) as well as PayPal. For Enterprise plans, we can also accommodate purchase orders and bank transfers.",
            },
            {
                "question": "Is there a long-term contract?",
                "answer": "No. All our plans are month-to-month, and you can cancel at any time. For Enterprise customers, we do offer discounted annual plans if you prefer.",
            },
        ]

        return context


class BlogView(MainContentView):
    """Blog index page."""

    template_name = "pages/blog.html"
    active_nav = "blog"  # Add this to active_navigation context processor

    def get(self, request, *args, **kwargs):
        """Get method to load blog content into context."""
        self.context.update(self.get_context_data())
        return self.render(request)

    def get_context_data(self, **kwargs):
        """Add blog posts to the context."""
        context = {}

        # Example blog posts - in a real app, these would come from a database
        context["posts"] = [
            {
                "title": "Getting Started with Our Platform",
                "slug": "getting-started",
                "excerpt": "Learn how to get up and running with our platform in just a few minutes.",
                "published_at": timezone.now() - timezone.timedelta(days=2),
                "author": "Jane Smith",
                "image": "https://placehold.co/800x450/354a96/FFFFFF/png?text=Getting+Started",
                "tags": ["tutorial", "beginners"],
                "category": "Tutorials",
            },
            {
                "title": "Advanced Features You Might Have Missed",
                "slug": "advanced-features",
                "excerpt": "Discover these powerful but often overlooked features to supercharge your workflow.",
                "published_at": timezone.now() - timezone.timedelta(days=7),
                "author": "John Davis",
                "image": "https://placehold.co/800x450/2a6546/FFFFFF/png?text=Advanced+Features",
                "tags": ["advanced", "tips"],
                "category": "Tips & Tricks",
            },
            {
                "title": "Customer Success Story: How Company X Increased Productivity by 200%",
                "slug": "success-story-company-x",
                "excerpt": "See how Company X used our platform to revolutionize their workflow and achieve amazing results.",
                "published_at": timezone.now() - timezone.timedelta(days=14),
                "author": "Sarah Johnson",
                "image": "https://placehold.co/800x450/8b4513/FFFFFF/png?text=Success+Story",
                "tags": ["case study", "success story"],
                "category": "Case Studies",
            },
            {
                "title": "Upcoming Features: What's New in Q2 2025",
                "slug": "upcoming-features-q2-2025",
                "excerpt": "Get a sneak peek at the exciting new features we're launching in the next quarter.",
                "published_at": timezone.now() - timezone.timedelta(days=21),
                "author": "Michael Brown",
                "image": "https://placehold.co/800x450/793963/FFFFFF/png?text=New+Features",
                "tags": ["roadmap", "features"],
                "category": "Announcements",
            },
            {
                "title": "5 Tips to Optimize Your Workflow",
                "slug": "optimize-workflow",
                "excerpt": "Simple but effective strategies to make the most of our platform and boost your productivity.",
                "published_at": timezone.now() - timezone.timedelta(days=30),
                "author": "Lisa Chen",
                "image": "https://placehold.co/800x450/3d5a80/FFFFFF/png?text=5+Tips",
                "tags": ["productivity", "tips"],
                "category": "Tips & Tricks",
            },
        ]

        # Categories for sidebar filtering
        context["categories"] = [
            {"name": "Tutorials", "count": 12},
            {"name": "Tips & Tricks", "count": 8},
            {"name": "Case Studies", "count": 5},
            {"name": "Announcements", "count": 3},
            {"name": "Industry News", "count": 7},
        ]

        # Recent posts for sidebar
        context["recent_posts"] = context["posts"][:3]

        # Popular tags for sidebar
        context["tags"] = [
            {"name": "tutorial", "count": 15},
            {"name": "tips", "count": 12},
            {"name": "productivity", "count": 9},
            {"name": "case study", "count": 6},
            {"name": "features", "count": 5},
            {"name": "beginners", "count": 8},
            {"name": "advanced", "count": 7},
        ]

        return context


class BlogPostView(MainContentView):
    """Individual blog post page."""

    template_name = "pages/blog_post.html"
    active_nav = "blog"  # Add this to active_navigation context processor

    def get(self, request, *args, **kwargs):
        """
        Handle GET request for blog post detail.

        Args:
            request: HTTP request
            *args: Variable-length argument list
            **kwargs: Arbitrary keyword arguments including 'slug' from URL

        Returns:
            HttpResponse: Rendered response
        """
        slug = kwargs.get("slug")
        self.context["post"] = self._get_post_by_slug(slug)
        return self.render(request)

    def _get_post_by_slug(self, slug):
        """
        Get blog post data based on slug.

        Args:
            slug: Post slug from URL

        Returns:
            dict: Post data

        Raises:
            Http404: If post not found
        """
        # In a real app, you would fetch the post from the database
        # Here we'll use dummy data based on the slug
        if slug == "sample-post":
            return {
                "title": "Sample Blog Post",
                "slug": "sample-post",
                "excerpt": "A sample blog post demonstrating the formatting and structure.",
                "content": """
                <p>This is a sample blog post with multiple paragraphs and formatting.</p>

                <h2>Section Heading</h2>
                <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nullam euismod,
                nisl eget aliquam ultricies, nunc nisl aliquet nunc, quis aliquam nisl
                nunc quis nisl. Nullam euismod, nisl eget aliquam ultricies, nunc nisl
                aliquet nunc, quis aliquam nisl nunc quis nisl.</p>

                <p>Nulla facilisi. Nullam euismod, nisl eget aliquam ultricies, nunc nisl
                aliquet nunc, quis aliquam nisl nunc quis nisl. Nullam euismod, nisl eget
                aliquam ultricies, nunc nisl aliquet nunc, quis aliquam nisl nunc quis nisl.</p>

                <h2>Another Section</h2>
                <p>Here's some code example:</p>
                <pre><code>def hello_world():
    print("Hello, world!")
                </code></pre>

                <p>And here's a list of key points:</p>
                <ul>
                    <li>First important point</li>
                    <li>Second important point</li>
                    <li>Third important point with more detail and explanation</li>
                </ul>

                <blockquote>
                    This is a blockquote with an important insight or quote from an expert.
                </blockquote>

                <h2>Conclusion</h2>
                <p>In conclusion, this is a sample blog post that demonstrates the formatting
                and structure of blog posts on our platform.</p>
                """,
                "published_at": timezone.now() - timezone.timedelta(days=2),
                "author": {
                    "name": "Jane Smith",
                    "avatar": "https://placehold.co/48x48/555555/FFFFFF/png?text=JS",
                    "bio": "Jane is a senior developer and technical writer with over 10 years of experience.",
                },
                "image": "https://placehold.co/1200x600/4a5859/FFFFFF/png?text=Sample+Blog+Post",
                "tags": ["tutorial", "beginners"],
                "category": "Tutorials",
                "related_posts": [
                    {
                        "title": "Getting Started with Our Platform",
                        "slug": "getting-started",
                        "image": "https://placehold.co/400x225/354a96/FFFFFF/png?text=Getting+Started",
                    },
                    {
                        "title": "5 Tips to Optimize Your Workflow",
                        "slug": "optimize-workflow",
                        "image": "https://placehold.co/400x225/3d5a80/FFFFFF/png?text=5+Tips",
                    },
                    {
                        "title": "Advanced Features You Might Have Missed",
                        "slug": "advanced-features",
                        "image": "https://placehold.co/400x225/2a6546/FFFFFF/png?text=Advanced+Features",
                    },
                ],
            }
        else:
            # If we don't have a post for this slug, return 404
            raise Http404("Blog post not found")
