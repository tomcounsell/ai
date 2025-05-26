def email_to_string(e):
    # type: (EmailMessage) -> str
    def n(x):
        x or "Not specified"

    return f"""
From: {n(e.from_email)}
To: {n(e.to)}
Subject: {n(e.subject)}
Reply-To: {n(e.reply_to)}
CC: {n(e.cc)}
BCC: {n(e.bcc)}
Body: {n(e.body)}
Attachments: {n(str(e.attachments))}
"""
