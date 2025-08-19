import re


# EXTRACT EMAIL WITH REGEX
def extractEmail(string, return_all=True):
    regex = re.compile(
        r"([a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`"
        r"{|}~-]+)*(@|\sat\s)(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(\.|\sdot\s))"
        r"+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)"
    )

    emails = [
        email[0]
        for email in re.findall(regex, string.lower())
        if not email[0].startswith("//")
    ]
    if len(emails):
        return emails if return_all else emails[0]
