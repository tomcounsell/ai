import sys

from systems.structures.redis_storage.key_value import KeyValueStorage
from django.test import TestCase

lisa_info_dict = {
    'generic': "mammal:human:woman",
    'group': "BlackPink",
    'friends': ["Jisoo", "Rose", "Jenny", ],
    'age': 24,
    'specifics': {'favorite:color': "yellow", },
}


class test_key_value(TestCase):
    default_object_storage = KeyValueStorage()
    basic_object_storage = KeyValueStorage(key="Lisa")
    general_object_storage = KeyValueStorage(key_prefix=lisa_info_dict['generic'], key="Lisa")
    specific_object_storage = KeyValueStorage(key="Lisa", key_suffix=list(lisa_info_dict['specifics'].keys())[0])

    def setup(self):
        self.assertEqual('utf-8', sys.getdefaultencoding())

    def test_can_manage_db_key(self):
        pass

    def test_can_store_values(self):
        self.default_object_storage.value = "this is the value of a testing class"
        self.default_object_storage.save()
        self.retrieved_object = KeyValueStorage()
        self.assertEqual(self.retrieved_object.value, "this is the value of a testing class")

        self.basic_object_storage.value = "super awesome"
        self.basic_object_storage.save()
        self.retrieved_object = KeyValueStorage(key="Lisa")
        self.assertEqual(self.retrieved_object.value, "super awesome")

        self.general_object_storage.value = lisa_info_dict
        self.general_object_storage.save()
        self.retrieved_object = KeyValueStorage(key="Lisa", key_prefix='mammal:human:woman')
        self.assertEqual(self.retrieved_object.value, lisa_info_dict)

        self.specific_object_storage.value = list(lisa_info_dict['specifics'].values())[0]
        self.specific_object_storage.save()
        self.retrieved_object = KeyValueStorage(key="Lisa", key_suffix='favorite:color')
        self.assertEqual(self.retrieved_object.value, "yellow")
