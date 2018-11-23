import logging
from os import path
from typing import Dict
from urllib.parse import urlparse

import requests

# Using beta api to support External Tickets
API_ENDPOINT = 'https://api.clubhouse.io/api/v2'

ClubhouseStory = Dict[str, object]
ClubhouseUser = Dict[str, object]
ClubhouseComment = Dict[str, str]
ClubhouseTask = Dict[str, str]
ClubhouseLabel = Dict[str, str]
ClubhouseFile = Dict[str, str]

logger = logging.getLogger('clubhouse')


class ClubhouseClient(object):
    def __init__(self, api_key, ignored_status_codes=None):
        self.ignored_status_codes = ignored_status_codes or []
        self.api_key = api_key

    def get(self, *segments, **kwargs):
        return self._request('get', *segments, **kwargs)

    def post(self, *segments, **kwargs):
        return self._request('post', *segments, **kwargs)

    def put(self, *segments, **kwargs):
        return self._request('put', *segments, **kwargs)

    def delete(self, *segments, **kwargs):
        return self._request('delete', *segments, **kwargs)

    def _request(self, method, *segments, **kwargs):
        url = path.join(API_ENDPOINT, *[str(s) for s in segments])
        prefix = "&" if urlparse(url)[4] else "?"
        response = requests.request(method, url + f"{prefix}token={self.api_key}", **kwargs)
        if response.status_code > 299 and response.status_code not in self.ignored_status_codes:
            logger.error(f"Status code: {response.status_code}, Content: {response.text}")
            response.raise_for_status()
        if response.status_code == 204:
            return {}
        return response.json()
