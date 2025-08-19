import logging
from typing import Optional

import requests
from icecream import ic

from settings import DEBUG, LOOPS_API_KEY

logger = logging.getLogger(__name__)


class LoopsClient:
    BASE_URL = "https://app.loops.so/api/v1"

    def __init__(self, api_key: str | None = None, debug_mode: bool | None = None):
        self.api_key = api_key or LOOPS_API_KEY
        # Use provided debug_mode if specified, otherwise use Django's DEBUG setting
        self.debug_mode = debug_mode if debug_mode is not None else DEBUG

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        """
        Make an API request to Loops.so

        :param method: HTTP method for the request
        :param endpoint: API endpoint
        :param params: Query parameters for the request
        :param json: JSON data for the request body
        :return: Parsed JSON response
        """
        # In debug mode, just log the request details and return success
        if self.debug_mode:
            logger.info(
                f"[DEBUG MODE] Loops API request would have been: {method} {endpoint}"
            )
            logger.info(f"[DEBUG MODE] Headers: Authorization: Bearer {self.api_key}")
            logger.info(f"[DEBUG MODE] Params: {params}")
            logger.info(f"[DEBUG MODE] JSON: {json}")
            return {"success": True}

        url = f"{self.BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        ic(method, url, params, json)

        try:
            response = requests.request(
                method, url, headers=headers, params=params, json=json
            )
            # response.raise_for_status()
            response_dict = response.json()
            if response_dict.get("success", False):
                return response_dict
            else:
                ic(response_dict)
                raise LoopsAPIError(
                    f"API request failed: {response_dict.get('message', 'unknown')}"
                )

        except requests.RequestException as e:
            raise LoopsAPIError(f"API request failed: {str(e)}")

    def test_api_key(self) -> dict:
        """
        Test the API key

        :return: API response
        """
        response = self._make_request(method="GET", endpoint="/api-key")
        ic(response)
        return response

    def transactional_email(
        self,
        to_email: str,
        transactional_id: str,
        data_variables: dict = None,
        **kwargs,
    ) -> dict:
        """
        Send a transactional email

        :param str to_email: The contact's email address. If there is no contact
                          with this email, one will be created.
        :param str transactional_id: The ID of the transactional email to send.
        :param str data_variables: An object containing contact data as defined
                                  by the data variables added to the
                                  transactional email template.
        """
        # In debug mode, just log the request details and return success
        if self.debug_mode:
            logger.info("[DEBUG MODE] Would have sent transactional email:")
            logger.info(f"[DEBUG MODE] To: {to_email}")
            logger.info(f"[DEBUG MODE] Template ID: {transactional_id}")
            logger.info(f"[DEBUG MODE] Data variables: {data_variables}")
            if kwargs.get("bcc"):
                logger.info(f"[DEBUG MODE] BCC: {kwargs.get('bcc')}")
            return {"success": True}

        json_data = {
            "email": to_email,
            "transactionalId": transactional_id,
            "dataVariables": data_variables or {},
        }

        # Add BCC if provided
        if kwargs.get("bcc"):
            json_data["bcc"] = kwargs.get("bcc")

        return self._make_request(
            method="POST",
            endpoint="/transactional",
            json=json_data,
        )

    def event(
        self, to_email: str, event_name: str, event_properties: dict | None = None
    ) -> dict:
        """
        Send an event to Loops

        :param str to_email: The contact's email address. If there is no contact
                          with this email, one will be created.
        :param str event_name: The name of the event
        """
        # In debug mode, just log the request details and return success
        if self.debug_mode:
            logger.info("[DEBUG MODE] Would have sent Loops event:")
            logger.info(f"[DEBUG MODE] To: {to_email}")
            logger.info(f"[DEBUG MODE] Event: {event_name}")
            logger.info(f"[DEBUG MODE] Properties: {event_properties}")
            return {"success": True}

        return self._make_request(
            method="POST",
            endpoint="/events/send",
            json={
                "email": to_email,
                "eventName": event_name,
                "eventProperties": event_properties or {},
            },
        )


class LoopsAPIError(Exception):
    """Custom exception for Loops API errors"""

    pass
