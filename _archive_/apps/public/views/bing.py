from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views.generic import View

from apps.search.bing import Bing


class BingView(View):
    def dispatch(self, request, *args, **kwargs):
        self.context = {}
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        search_terms = request.GET.get('search_terms')
        self.context['search_terms'] = search_terms
        if search_terms:
            bing = Bing()
            bing.search(search_terms)
            self.context['images'] = bing.get_images(search_terms)

        return render(request, 'bing.html', self.context)

