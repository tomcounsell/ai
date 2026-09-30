from slugify import slugify


def test_basic():
    assert slugify("Hello World") == "hello-world"


def test_punctuation_collapses():
    assert slugify("a, b; c!") == "a-b-c"


def test_unicode_folds():
    assert slugify("Crème brûlée") == "creme-brulee"
