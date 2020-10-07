from django.conf.urls import include, url
from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView
from settings import DEBUG

urlpatterns = [

    # static page
    # path('', TemplateView.as_view(template_name='home.html'), name="home"),

    # route prefix for urlpatterns in apps/dashboard/urls.py
    # path('dashboard/', include('apps.dashboard.urls', namespace='dashboard')),
    # if using namespace, include app_name = "dashboard" in urls.py

]

# Django Rest Framework API Docs
from rest_framework.documentation import include_docs_urls
API_TITLE, API_DESCRIPTION = "Company API", ""
urlpatterns += [
    url(r'^docs/', include_docs_urls(title=API_TITLE, description=API_DESCRIPTION))
]


# Built-In AUTH and ADMIN
admin.autodiscover()
admin.site.site_header = "Company Content Database"
admin.site.site_title = "Company"
admin.site.site_url = None
admin.site.index_title = "Content Database"

urlpatterns += [
    url(r'^admin/', admin.site.urls),
]


# # JWT AUTH
# from rest_framework_simplejwt import views as jwt_views
# urlpatterns += [
#     path('token/', jwt_views.TokenObtainPairView.as_view(), name='token_obtain_pair'),
#     path('token/refresh/', jwt_views.TokenRefreshView.as_view(), name='token_refresh'),
#     path('token/verify/', jwt_views.TokenVerifyView.as_view(), name='token_verify'),
# ]


# SCHEDULED TASKS
# urlpatterns += [
#     url(r'^scheduled/v1/some_script',
#         some_util_file.CompilerView.as_view(),
#         name="some_script"),
# ]


# DEBUG MODE use debug_toolbar
if DEBUG:
    from django.urls import include, path  # For django versions from 2.0 and up
    import debug_toolbar

    urlpatterns = [path('__debug__/', include(debug_toolbar.urls)), ] + urlpatterns
