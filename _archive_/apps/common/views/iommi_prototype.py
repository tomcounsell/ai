import iommi
from iommi import *

class Page(iommi.Page):
    pass


class Action(iommi.Action):
    pass


class Field(iommi.Field):
    pass


class Form(iommi.Form):

    def _from_model(cls, exclude=None, *args, **kwargs):
        exclude = ['created_at', 'modified_at'] + (exclude if exclude is not None else [])
        return super()._from_model(exclude=exclude, *args, **kwargs)


    class Meta:
        member_class = Field
        page_class = Page
        action_class = Action


class Filter(iommi.Filter):
    pass


class Query(iommi.Query):
    class Meta:
        member_class = Filter
        form_class = Form


class Column(iommi.Column):
    pass


class Table(iommi.Table):
    class Meta:
        member_class = Column
        form_class = Form
        query_class = Query
        page_class = Page
        action_class = Action


class Menu(iommi.Menu):
    pass


class MenuItem(iommi.MenuItem):
    pass
