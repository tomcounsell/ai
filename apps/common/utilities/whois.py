import whois
import logging
logger = logging.getLogger(__name__)

def get_domain_owner_emails(domain):
    w = whois.whois(domain)
    try:
        return w.emails
    except Exception as e:
        logger.debug("error searching for domain emails: %s" % str(e))
        return []
