from django.core.mail import EmailMessage


def email_to_string(e):
    # type: (EmailMessage) -> str
    def n(x):
        x or 'Not specified'

    return """
From: {}
To: {}
Subject: {}
Reply-To: {}
CC: {}
BCC: {}
Body: {}
Attachments: {}
""".format(
        n(e.from_email),
        n(e.to),
        n(e.subject),
        n(e.reply_to), n(e.cc), n(e.bcc), n(e.body), n(str(e.attachments)))
